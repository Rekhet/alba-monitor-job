"""Bounded public HTTP and ordinary, non-authenticated Chromium transport."""

import time
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup

from .config import Config
from .parsers import HOSTS, canonical_url, document


class AccessError(RuntimeError):
    pass


def check_url(source: str, url: str) -> None:
    parsed = urlparse(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname != HOSTS.get(source)
        or parsed.port not in (None, 443)
        or parsed.username
    ):
        raise ValueError("unexpected source URL")
    listing_paths = {
        "albamon": {"/jobs/home", "/jobs/area/home", "/jobs/total"},
        "alba": {"/job/Main"},
    }
    if parsed.path in listing_paths[source]:
        return
    canonical_url(source, url)


class SourceClient:
    def __init__(self, source: str, config: Config):
        self.source, self.config = source, config
        self._last_request = None
        self._playwright = self._browser = self._page = None
        self._frame_response_status = None

    def _record_response(self, response):
        if urlparse(response.url).path == "/job/DetailContent":
            self._frame_response_status = response.status

    def _pace(self):
        if self._last_request is not None:
            time.sleep(
                max(
                    0,
                    self.config.request_delay_seconds
                    - (time.monotonic() - self._last_request),
                )
            )
        self._last_request = time.monotonic()

    def _http(self, url: str) -> str:
        self._pace()
        request = Request(
            url, headers={"User-Agent": "AlbaCollector/0.1 (personal job research)"}
        )
        with urlopen(request, timeout=self.config.timeout_seconds) as response:
            check_url(self.source, response.url)
            if response.status != 200:
                raise AccessError(f"HTTP {response.status}")
            content = response.read(10_000_001)
            if len(content) > 10_000_000:
                raise AccessError("response exceeded 10 MB")
            return content.decode(
                response.headers.get_content_charset() or "utf-8", errors="replace"
            )

    def _open(self, url: str) -> str:
        check_url(self.source, url)
        if self.source == "albamon":
            html = self._http(url)
        else:
            if self._playwright is None:
                from playwright.sync_api import sync_playwright

                self._playwright = sync_playwright().start()
                self._browser = self._playwright.chromium.launch(headless=True)
                self._page = self._browser.new_page()
                self._page.on("response", self._record_response)
                self._page.set_default_timeout(self.config.timeout_seconds * 1000)
            self._pace()
            self._frame_response_status = None
            response = self._page.goto(url, wait_until="domcontentloaded")
            check_url(self.source, self._page.url)
            if response is None or response.status != 200:
                raise AccessError(
                    f"HTTP {response.status if response else 'no response'}"
                )
            html = self._page.content()
        document(html)
        return html

    def listing(self, url: str) -> str:
        return self._open(url)

    def detail(self, url: str, collect_body: bool) -> tuple[str, str | None]:
        html = self._open(canonical_url(self.source, url))
        if self.source != "alba" or not collect_body:
            return html, None
        from playwright.sync_api import Error as PlaywrightError

        try:
            return html, self._description_html(url, html)
        except (AccessError, ValueError, PlaywrightError):
            return html, ""

    def _description_html(self, url: str, html: str) -> str:
        soup = BeautifulSoup(html, "html.parser")
        frame = soup.select_one('iframe[title="상세 모집내용 프레임"]')
        if frame is None:
            return ""
        parsed = urlparse(frame.get("src", ""))
        detail_id = parse_qs(urlparse(url).query)["adid"]
        if (
            parsed.path != "/job/DetailContent"
            or (parsed.netloc and parsed.hostname != HOSTS[self.source])
            or parse_qs(parsed.query).get("adid") != detail_id
        ):
            raise AccessError("unexpected description frame")
        body = self._page.frame_locator('iframe[title="상세 모집내용 프레임"]').locator(
            "body"
        )
        body.wait_for(state="attached")
        element = self._page.locator(
            'iframe[title="상세 모집내용 프레임"]'
        ).element_handle()
        iframe = element.content_frame() if element else None
        if iframe is None:
            raise AccessError("description frame disappeared")
        final = urlparse(iframe.url)
        if (
            final.hostname != HOSTS[self.source]
            or final.path != "/job/DetailContent"
            or parse_qs(final.query).get("adid") != detail_id
            or self._frame_response_status != 200
        ):
            raise AccessError(
                "description frame did not return a public job description"
            )
        content = body.inner_html()
        document(content)
        return content

    def close(self):
        if self._browser is not None:
            self._browser.close()
        if self._playwright is not None:
            self._playwright.stop()
