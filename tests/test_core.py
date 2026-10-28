import importlib
import json
from dataclasses import replace

import pytest


def api(name):
    try:
        return importlib.import_module(f"alba_collector.{name}")
    except ModuleNotFoundError:
        pytest.fail(f"{name}: implementation not yet available")


MON_URL = "https://www.albamon.com/jobs/detail/12345"
ALBA_URL = "https://www.alba.co.kr/job/Detail?adid=54321"
STAMP = "2026-10-28T08:00:00+00:00"


def mon_html():
    data = {
        "@type": "JobPosting",
        "title": "카페 주말 보조",
        "hiringOrganization": {"name": "테스트카페"},
        "jobLocation": {
            "address": {"addressRegion": "서울", "streetAddress": "마포구 테스트로 1"}
        },
        "baseSalary": {
            "currency": "KRW",
            "value": {"value": 12500, "unitText": "HOUR"},
        },
        "description": "업직종: 바리스타",
        "datePosted": "2026-10-27",
        "validThrough": "2026-11-02",
    }
    return (
        '<title>카페 채용 | 알바몬</title><script type="application/ld+json">'
        + json.dumps(data, ensure_ascii=False)
        + "</script>"
        + """
    <dl><dt>급여</dt><dd>시급 12,500원 <p>2026년 최저시급 10,320원</p></dd></dl>
    <dl><dt>근무요일</dt><dd>주말(토,일)</dd></dl><dl><dt>근무시간</dt><dd>09:00~15:00</dd></dl>
    <dl><dt>업직종</dt><dd>바리스타</dd></dl>
    <iframe title="상세 모집 요강" src="/jobs/detail/content/12345"></iframe>"""
    )


def test_config_rejects_unknown_keys_before_collection(tmp_path):
    config = tmp_path / "bad.toml"
    config.write_text('regons = ["서울"]', encoding="utf-8")
    with pytest.raises(ValueError, match="regons"):
        api("config").load_config(config)


@pytest.mark.parametrize(
    "setting",
    [
        "pages_per_source = 0",
        "max_details_per_source = -1",
        "timeout_seconds = true",
        'sources = ["unknown"]',
        'regions = ["서울", "서울"]',
    ],
)
def test_config_rejects_invalid_limits_and_duplicates(tmp_path, setting):
    config = tmp_path / "bad.toml"
    config.write_text(setting, encoding="utf-8")
    with pytest.raises(ValueError):
        api("config").load_config(config)


def test_listing_deduplicates_tracking_and_rejects_external_urls():
    html = """<a href="/jobs/detail/12345?sc=1">공고</a><a href="/jobs/detail/12345?sc=2">새창</a>
    <a href="https://evil.example/jobs/detail/12345">외부</a><a href="/jobs/detail/content/12345">본문</a>"""
    assert api("parsers").listing_urls(
        "albamon", html, "https://www.albamon.com/jobs/home"
    ) == [MON_URL]


def test_albamon_structured_salary_does_not_use_minimum_wage():
    job = api("parsers").parse_detail("albamon", mon_html(), MON_URL, STAMP)
    assert (job.source_id, job.salary_amount, job.salary_unit) == (
        "12345",
        12500,
        "hour",
    )
    assert job.company == "테스트카페"
    assert job.location == "서울 마포구 테스트로 1"
    assert job.work_days == "주말(토,일)"
    assert job.body_status == "policy_excluded"
    assert job.body_text is None


def test_alba_body_is_separate_from_navigation_and_contacts():
    html = """<title>테스트가게 채용정보 : 직원 모집 - 알바천국</title>
    <h1>직원 모집</h1><dl><dt>근무지명</dt><dd>테스트가게</dd></dl>
    <dl><dt>급여</dt><dd>시급 13,000 원</dd></dl><dl><dt>근무기간</dt><dd>3개월</dd></dl>
    <dl><dt>근무요일</dt><dd>주5일</dd></dl><dl><dt>근무시간</dt><dd>시간협의</dd></dl>
    <dl><dt>근무지</dt><dd>경기 성남시 분당구</dd></dl><dl><dt>모집직종</dt><dd>주방보조</dd></dl>"""
    body = "<body><p>조리보조 및 배식</p><script>bad()</script><p>시간 협의</p></body>"
    job = api("parsers").parse_detail("alba", html, ALBA_URL, STAMP, body_html=body)
    assert job.salary_amount == 13000
    assert job.location == "경기 성남시 분당구"
    assert job.body_text == "조리보조 및 배식 시간 협의"
    assert job.body_status == "text"
    assert job.source_id == "54321"


def test_body_images_are_not_reported_as_text():
    parser = api("parsers")
    html = "<title>가게 채용정보 : 직원 모집 - 알바천국</title><h1>직원 모집</h1><dl><dt>급여</dt><dd>시급 협의</dd></dl>"
    job = parser.parse_detail(
        "alba", html, ALBA_URL, STAMP, body_html='<body><img src="poster.png"></body>'
    )
    assert job.body_status == "image_only"
    assert job.salary_amount is None


@pytest.mark.parametrize(
    "html",
    [
        "<h1>알바천국 접속에 실패 했습니다.</h1>",
        "<h1>로그인이 필요합니다</h1>",
        "<title>알바몬</title>",
        "<h1>마감된 공고입니다</h1>",
    ],
)
def test_error_pages_are_not_accepted_as_jobs(html):
    parser = api("parsers")
    with pytest.raises(parser.ParseError):
        parser.parse_detail("alba", html, ALBA_URL, STAMP)


def test_region_filter_uses_detail_location_and_keeps_unknown():
    config = api("config").Config(regions=("서울", "경기"))
    job = api("parsers").parse_detail("albamon", mon_html(), MON_URL, STAMP)
    assert config.matches(job)
    assert not config.matches(replace(job, location="부산 해운대구"))
    assert config.matches(replace(job, location=None))
    assert (
        api("config").Config(industries=("주방",)).matches(replace(job, industry=None))
    )
    assert not api("config").Config(industries=("주방",)).matches(job)


def test_sqlite_upsert_is_idempotent_and_preserves_change_history(tmp_path):
    job = api("parsers").parse_detail("albamon", mon_html(), MON_URL, STAMP)
    storage = api("storage")
    with storage.SQLiteStore(tmp_path / "jobs.sqlite3") as store:
        assert store.save(job) == "inserted"
        assert (
            store.save(replace(job, observed_at="2026-10-28T09:00:00+00:00"))
            == "unchanged"
        )
        assert (
            store.save(
                replace(
                    job, salary_amount=14000, observed_at="2026-10-28T10:00:00+00:00"
                )
            )
            == "updated"
        )
        rows = store.query(limit=10)
        assert len(rows) == 1
        assert rows[0]["salary_amount"] == 14000
        assert rows[0]["first_seen"] == STAMP
        assert (
            store.connection.execute("select count(*) from job_versions").fetchone()[0]
            == 2
        )


def test_storage_rejects_older_observations_without_overwriting(tmp_path):
    job = api("parsers").parse_detail("albamon", mon_html(), MON_URL, STAMP)
    with api("storage").SQLiteStore(tmp_path / "jobs.sqlite3") as store:
        store.save(job)
        assert (
            store.save(
                replace(job, salary_amount=1, observed_at="2026-10-27T08:00:00+00:00")
            )
            == "stale"
        )
        assert store.query(limit=10)[0]["salary_amount"] == 12500


def test_salary_negotiable_does_not_become_statutory_minimum():
    html = "<title>가게 채용정보 : 직원 모집 - 알바천국</title><dl><dt>급여</dt><dd>시급 협의 <p>2026년 최저시급 10,320원</p></dd></dl>"
    job = api("parsers").parse_detail("alba", html, ALBA_URL, STAMP)
    assert job.salary_amount is None


def test_iframe_error_page_is_rejected():
    parser = api("parsers")
    html = "<title>가게 채용정보 : 직원 모집 - 알바천국</title><dl><dt>급여</dt><dd>시급 협의</dd></dl>"
    with pytest.raises(parser.ParseError):
        parser.parse_detail(
            "alba",
            html,
            ALBA_URL,
            STAMP,
            body_html="<h1>알바천국 접속에 실패 했습니다.</h1>",
        )


def test_region_filter_does_not_match_road_names():
    job = api("parsers").parse_detail("albamon", mon_html(), MON_URL, STAMP)
    config = api("config").Config()
    assert not config.matches(replace(job, location="부산광역시 강서구 서울길 1"))
    assert config.matches(replace(job, location="서울특별시 마포구 테스트로 1"))
    assert config.matches(replace(job, location="경기도 성남시 테스트로 1"))


def test_structured_salary_survives_display_without_unit_prefix():
    html = mon_html().replace(
        "시급 12,500원 <p>2026년 최저시급 10,320원</p>", "12,500원"
    )
    job = api("parsers").parse_detail("albamon", html, MON_URL, STAMP)
    assert job.salary_amount == 12500
    assert job.salary_unit == "hour"
