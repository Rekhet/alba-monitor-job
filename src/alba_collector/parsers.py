"""Pure parsers: no requests, browser state, or persistence."""

import json
import re
from urllib.parse import parse_qs, urljoin, urlparse

from bs4 import BeautifulSoup

from .model import Job

HOSTS = {"albamon": "www.albamon.com", "alba": "www.alba.co.kr"}


class ParseError(ValueError):
    pass


def canonical_url(source: str, url: str) -> str:
    parsed = urlparse(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname != HOSTS.get(source)
        or parsed.port not in (None, 443)
        or parsed.username
    ):
        raise ParseError("unexpected source URL")
    if source == "albamon":
        match = re.fullmatch(r"/jobs/detail/(\d+)", parsed.path)
        if match:
            return f"https://{HOSTS[source]}/jobs/detail/{match[1]}"
    elif source == "alba" and parsed.path == "/job/Detail":
        identifiers = parse_qs(parsed.query).get("adid", [])
        if len(identifiers) == 1 and identifiers[0].isdigit():
            return f"https://{HOSTS[source]}/job/Detail?adid={identifiers[0]}"
    raise ParseError("not a public job detail URL")


def document(html: str) -> BeautifulSoup:
    soup = BeautifulSoup(html, "html.parser")
    heading_text = " ".join(
        node.get_text(" ", strip=True) for node in soup.select("h1,.error-guide")
    )
    if any(
        message in heading_text
        for message in (
            "접속에 실패",
            "로그인이 필요",
            "마감된 공고",
            "삭제된 공고",
            "존재하지 않는 공고",
        )
    ):
        raise ParseError("source returned an error, login, or closed-job page")
    return soup


def listing_urls(source: str, html: str, url: str) -> list[str]:
    links = []
    for element in document(html).find_all("a", href=True):
        try:
            target = canonical_url(source, urljoin(url, element["href"]))
        except (ParseError, ValueError):
            continue
        if target not in links:
            links.append(target)
    if not links:
        raise ParseError("listing contained no recognizable public job links")
    return links


def plain_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for node in soup(["script", "style", "noscript"]):
        node.decompose()
    return " ".join(soup.get_text(" ", strip=True).split())


def definition_fields(soup: BeautifulSoup) -> dict[str, str]:
    result = {}
    for label in soup.select("dt"):
        value = label.find_next_sibling("dd")
        if value is not None:
            result.setdefault(
                label.get_text(" ", strip=True), value.get_text(" ", strip=True)
            )
    return result


def job_posting(soup: BeautifulSoup) -> dict:
    def find(value):
        if isinstance(value, list):
            for item in value:
                yield from find(item)
        elif isinstance(value, dict):
            if value.get("@type") == "JobPosting" or "JobPosting" in (
                value.get("@type") if isinstance(value.get("@type"), list) else []
            ):
                yield value
            yield from find(value.get("@graph", []))

    for node in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(node.string or node.get_text())
        except (ValueError, TypeError):
            continue
        if result := next(find(data), None):
            return result
    return {}


def parse_detail(
    source: str, html: str, url: str, observed_at: str, body_html: str | None = None
) -> Job:
    url = canonical_url(source, url)
    soup = document(html)
    data = job_posting(soup)
    values = definition_fields(soup)
    title = data.get("title")
    company = (data.get("hiringOrganization") or {}).get("name")
    if not title and source == "alba":
        page_title = soup.title.get_text(" ", strip=True) if soup.title else ""
        if "채용정보 :" in page_title:
            company, title = page_title.split("채용정보 :", 1)
            title = title.removesuffix("- 알바천국").strip()
            company = company.strip()
    if (
        not isinstance(title, str)
        or not title.strip()
        or not (data or values.get("급여"))
    ):
        raise ParseError("detail is missing job title or employment conditions")
    location = values.get("근무지") or values.get("근무지역")
    if source == "alba" and not location:
        address_node = soup.select_one(".detail-address__text, .detail-address__addr")
        if address_node:
            location = address_node.get_text(" ", strip=True)
    locations = data.get("jobLocation", {})
    if isinstance(locations, list):
        locations = locations[0] if locations else {}
    address = locations.get("address", {}) if isinstance(locations, dict) else {}
    if isinstance(address, dict):
        parts = [
            address.get(key)
            for key in ("addressRegion", "addressLocality", "streetAddress")
        ]
        location = location or " ".join(str(p) for p in parts if p) or None
    elif isinstance(address, str):
        location = location or address
    salary_text = values.get("급여")
    amount = None
    unit = None
    salary = data.get("baseSalary")
    if isinstance(salary, dict) and salary.get("currency", "KRW") == "KRW":
        value = salary.get("value", {})
        if isinstance(value, dict):
            number = value.get("value")
            if (
                type(number) in (int, float)
                and number >= 0
                and float(number).is_integer()
            ):
                amount = int(number)
            unit = {"HOUR": "hour", "DAY": "day", "MONTH": "month", "YEAR": "year"}.get(
                str(value.get("unitText", "")).upper()
            )
    if salary_text:
        salary_candidate = re.split(r"(?:\d{4}\s*년\s*)?최저", salary_text, maxsplit=1)[
            0
        ]
        match = re.search(
            r"(시급|일급|월급|연봉|건별)\s*([\d,]+)\s*원?", salary_candidate
        )
        if match:
            amount = int(match[2].replace(",", ""))
            unit = {
                "시급": "hour",
                "일급": "day",
                "월급": "month",
                "연봉": "year",
                "건별": "task",
            }[match[1]]
        elif re.search(r"협의|미정|면접\s*후|회사\s*내규", salary_candidate):
            amount = None
    body = None
    status = "not_requested"
    if source == "albamon":
        status = (
            "policy_excluded"
            if soup.select_one('iframe[title="상세 모집 요강"]')
            else "unavailable"
        )
    elif body_html is not None:
        document(body_html)
        body = plain_text(body_html) or None
        status = (
            "text"
            if body
            else (
                "image_only"
                if BeautifulSoup(body_html, "html.parser").find("img")
                else "unavailable"
            )
        )
    source_id = (
        url.rsplit("/", 1)[1]
        if source == "albamon"
        else parse_qs(urlparse(url).query)["adid"][0]
    )
    return Job(
        source=source,
        source_id=source_id,
        url=url,
        title=title.strip(),
        observed_at=observed_at,
        company=company,
        location=location,
        industry=values.get("업직종") or values.get("모집직종"),
        salary_amount=amount,
        salary_unit=unit,
        salary_text=salary_text,
        work_days=values.get("근무요일"),
        work_hours=values.get("근무시간") or data.get("workHours"),
        work_period=values.get("근무기간"),
        posted_at=data.get("datePosted"),
        deadline=values.get("모집마감") or data.get("validThrough"),
        description=(
            plain_text(data["description"])
            if isinstance(data.get("description"), str)
            else None
        ),
        body_text=body,
        body_status=status,
    )
