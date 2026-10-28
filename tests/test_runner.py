from dataclasses import replace
import json
from pathlib import Path
import subprocess
import sys

import pytest

from test_core import api, mon_html, MON_URL


def test_transport_rejects_policy_excluded_and_cross_site_urls():
    transport = api("transport")
    for url in [
        "https://www.albamon.com/jobs/detail/content/12345",
        "https://evil.example/job/Main",
        "http://www.alba.co.kr/job/Main",
        "https://www.alba.co.kr/job/DetailContent?adid=1",
    ]:
        with pytest.raises(ValueError):
            transport.check_url("albamon", url)


def test_unknown_location_is_reported_and_off_region_jobs_are_filtered(tmp_path):
    config = api("config").Config(
        sources=("albamon",), pages_per_source=1, max_details_per_source=2
    )
    html = mon_html().replace(
        '"addressRegion": "서울", "streetAddress": "마포구 테스트로 1"',
        '"addressRegion": "부산", "streetAddress": "해운대구"',
    )

    class Client:
        def listing(self, url):
            return '<a href="/jobs/detail/12345">공고</a>'

        def detail(self, url, collect_body):
            return html, None

        def close(self):
            pass

    report = api("runner").collect(config, tmp_path, client_factory=lambda *_: Client())
    assert report["sources"]["albamon"]["filtered"] == 1
    assert report["sources"]["albamon"]["matched"] == 0
    assert report["ok"] is True


def test_partial_failure_keeps_successes_and_marks_run_failed(tmp_path):
    config = api("config").Config(pages_per_source=1, max_details_per_source=1)

    class Client:
        def __init__(self, source):
            self.source = source

        def listing(self, url):
            if self.source == "alba":
                raise ValueError("source access failed")
            return '<a href="/jobs/detail/12345">공고</a>'

        def detail(self, url, collect_body):
            return mon_html(), None

        def close(self):
            pass

    runner = api("runner")
    report = runner.collect(
        config, tmp_path, client_factory=lambda source, _: Client(source)
    )
    assert report["ok"] is False
    assert report["sources"]["albamon"]["matched"] == 1
    assert report["sources"]["alba"]["status"] == "failed"
    exported = [
        json.loads(line) for line in (tmp_path / "jobs.jsonl").read_text().splitlines()
    ]
    assert len(exported) == 1
    assert exported[0]["url"] == MON_URL


def test_probe_never_saves_job_content(tmp_path):
    config = api("config").Config(
        sources=("albamon",), pages_per_source=1, max_details_per_source=1
    )

    class Client:
        def listing(self, url):
            return '<a href="/jobs/detail/12345">공고</a>'

        def detail(self, url, collect_body):
            return mon_html(), None

        def close(self):
            pass

    report = api("runner").collect(
        config, tmp_path, probe=True, client_factory=lambda *_: Client()
    )
    assert report["ok"]
    assert not (tmp_path / "jobs.sqlite3").exists()
    assert not (tmp_path / "jobs.jsonl").exists()
    serialized = json.dumps(report, ensure_ascii=False)
    assert "테스트카페" not in serialized
    assert "카페 주말 보조" not in serialized
    assert report["sources"]["albamon"]["body_statuses"] == {"policy_excluded": 1}


def test_probe_explicitly_reports_body_coverage_gap(tmp_path):
    config = api("config").Config(
        sources=("albamon",), pages_per_source=1, max_details_per_source=1
    )

    class Client:
        def listing(self, url):
            return '<a href="/jobs/detail/12345">공고</a>'

        def detail(self, url, collect_body):
            return mon_html(), None

        def close(self):
            pass

    report = api("runner").collect(
        config, tmp_path, probe=True, client_factory=lambda *_: Client()
    )
    assert report["ok"]
    assert report["body_complete"] is False
    assert report["sources"]["albamon"]["body_complete"] is False


def test_discovery_uses_nationwide_listing_instead_of_seoul_default(tmp_path):
    config = api("config").Config(
        sources=("albamon",), pages_per_source=1, max_details_per_source=1
    )

    class Client:
        def listing(self, url):
            if "/jobs/total?" in url:
                return '<a href="/jobs/detail/12345">공고</a>'
            raise ValueError("default regional discovery cannot cover Gyeonggi")

        def detail(self, url, collect_body):
            return mon_html(), None

        def close(self):
            pass

    report = api("runner").collect(
        config, tmp_path, probe=True, client_factory=lambda *_: Client()
    )
    assert report["ok"]
    assert report["sources"]["albamon"]["parsed"] == 1


def test_parse_failure_does_not_succeed_as_empty_collection(tmp_path):
    config = api("config").Config(
        sources=("albamon",), pages_per_source=1, max_details_per_source=1
    )

    class Client:
        def listing(self, url):
            return '<a href="/jobs/detail/12345">공고</a>'

        def detail(self, url, collect_body):
            return "<title>알바몬</title>", None

        def close(self):
            pass

    report = api("runner").collect(
        config, tmp_path, probe=True, client_factory=lambda *_: Client()
    )
    assert not report["ok"]
    assert report["sources"]["albamon"]["errors"][0]["code"] == "ParseError"


def test_cli_invalid_config_exits_without_creating_output(tmp_path):
    api("cli")
    config = tmp_path / "bad.toml"
    config.write_text("pages_per_source = 0")
    output = tmp_path / "output"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "alba_collector",
            "collect",
            "--config",
            str(config),
            "--output",
            str(output),
        ],
        env={
            **__import__("os").environ,
            "PYTHONPATH": str(Path(__file__).parents[1] / "src"),
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "pages_per_source" in result.stderr
    assert not output.exists()


def test_alba_location_is_read_from_address_block_without_dt():
    parser = api("parsers")
    html = '<title>가게 채용정보 : 직원 모집 - 알바천국</title><dl><dt>급여</dt><dd>시급 13,000원</dd></dl><div class="detail-address"><p class="detail-address__text">경기 성남시 테스트로 1</p></div>'
    assert (
        parser.parse_detail(
            "alba",
            html,
            "https://www.alba.co.kr/job/Detail?adid=1",
            "2026-10-28T08:00:00+00:00",
        ).location
        == "경기 성남시 테스트로 1"
    )


def test_later_listing_failure_does_not_discard_discovered_jobs(tmp_path):
    config = api("config").Config(
        sources=("albamon",), pages_per_source=2, max_details_per_source=1
    )

    class Client:
        def listing(self, url):
            if "page=2" in url:
                raise ValueError("page unavailable")
            return '<a href="/jobs/detail/12345">공고</a>'

        def detail(self, url, collect_body):
            return mon_html(), None

        def close(self):
            pass

    report = api("runner").collect(
        config, tmp_path, probe=True, client_factory=lambda *_: Client()
    )
    assert not report["ok"]
    assert report["sources"]["albamon"]["parsed"] == 1
    assert report["sources"]["albamon"]["status"] == "partial"


def test_requested_missing_body_preserves_conditions_but_fails_run(tmp_path):
    config = api("config").Config(
        sources=("alba",), pages_per_source=1, max_details_per_source=1
    )

    class Client:
        def listing(self, url):
            return '<a href="/job/Detail?adid=54321">공고</a>'

        def detail(self, url, collect_body):
            return (
                "<title>가게 채용정보 : 직원 모집 - 알바천국</title><dl><dt>급여</dt><dd>시급 협의</dd></dl>",
                "",
            )

        def close(self):
            pass

    report = api("runner").collect(config, tmp_path, client_factory=lambda *_: Client())
    assert report["ok"] is False
    assert report["sources"]["alba"]["matched"] == 1
    assert (tmp_path / "jobs.sqlite3").exists()
    assert report["sources"]["alba"]["errors"][0]["code"] == "BodyUnavailable"


def test_albamon_missing_frame_is_partial_when_body_requested(tmp_path):
    config = api("config").Config(
        sources=("albamon",), pages_per_source=1, max_details_per_source=1
    )

    class Client:
        def listing(self, url):
            return '<a href="/jobs/detail/12345">공고</a>'

        def detail(self, url, collect_body):
            return (
                mon_html().replace(
                    '<iframe title="상세 모집 요강" src="/jobs/detail/content/12345"></iframe>',
                    "",
                ),
                None,
            )

        def close(self):
            pass

    report = api("runner").collect(
        config, tmp_path, probe=True, client_factory=lambda *_: Client()
    )
    assert not report["ok"]
    assert report["sources"]["albamon"]["status"] == "partial"


def test_description_timeout_preserves_parent_conditions(monkeypatch):
    from playwright.sync_api import TimeoutError as PlaywrightTimeout

    transport = api("transport")
    client = transport.SourceClient("alba", api("config").Config())
    html = '<iframe title="상세 모집내용 프레임" src="/job/DetailContent?adid=54321"></iframe>'
    monkeypatch.setattr(client, "_open", lambda _: html)

    class Page:
        def frame_locator(self, selector):
            return self

        def locator(self, selector):
            return self

        def wait_for(self, **kwargs):
            raise PlaywrightTimeout("description timeout")

    client._page = Page()
    parent, body = client.detail("https://www.alba.co.kr/job/Detail?adid=54321", True)
    assert parent == html
    assert body == ""
