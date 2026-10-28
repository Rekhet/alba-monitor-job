from collections import Counter
from contextlib import nullcontext
from datetime import datetime, timezone
from dataclasses import replace
import json
from pathlib import Path

from .config import Config
from .parsers import listing_urls, parse_detail
from .storage import SQLiteStore
from .transport import SourceClient


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def listing_url(source: str, page: int) -> str:
    if source == "albamon":
        return f"https://www.albamon.com/jobs/total?page={page}"
    return f"https://www.alba.co.kr/job/Main?page={page}"


def write_report(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def collect(
    config: Config, output: Path, *, probe: bool = False, client_factory=SourceClient
) -> dict:
    """Bounded scan; partial success is persisted but report.ok stays false."""
    report = {
        "schema_version": 1,
        "started_at": timestamp(),
        "probe": probe,
        "scan": "bounded",
        "sources": {},
        "ok": True,
    }
    context = nullcontext(None) if probe else SQLiteStore(output / "jobs.sqlite3")
    with context as store:
        for source in config.sources:
            stats = {
                "status": "ok",
                "pages": 0,
                "discovered": 0,
                "parsed": 0,
                "matched": 0,
                "filtered": 0,
                "unknown_location": 0,
                "body_statuses": {},
                "writes": {},
                "errors": [],
            }
            report["sources"][source] = stats
            client = None
            try:
                client = client_factory(source, config)
                links = []
                for page in range(1, config.pages_per_source + 1):
                    try:
                        url = listing_url(source, page)
                        found = listing_urls(source, client.listing(url), url)
                        stats["pages"] += 1
                        links.extend(link for link in found if link not in links)
                    except Exception as error:
                        stats["errors"].append(
                            {
                                "stage": "listing",
                                "page": page,
                                "code": type(error).__name__,
                            }
                        )
                        break
                stats["discovered"] = len(links)
                bodies, writes = Counter(), Counter()
                for url in links[: config.max_details_per_source]:
                    try:
                        html, body_html = client.detail(url, config.collect_body)
                        job = parse_detail(source, html, url, timestamp(), body_html)
                        if not config.collect_body:
                            job = replace(
                                job, body_status="not_requested", body_text=None
                            )
                        stats["parsed"] += 1
                        bodies[job.body_status] += 1
                        if config.collect_body and job.body_status in (
                            "unavailable",
                            "not_requested",
                        ):
                            stats["errors"].append(
                                {"stage": "body", "code": "BodyUnavailable"}
                            )
                        if not job.location:
                            stats["unknown_location"] += 1
                        if not config.matches(job):
                            stats["filtered"] += 1
                            continue
                        stats["matched"] += 1
                        if store is not None:
                            writes[store.save(job)] += 1
                    except Exception as error:
                        # Source content and exception strings can contain personal data or request tokens.
                        stats["errors"].append(
                            {"stage": "detail", "code": type(error).__name__}
                        )
                stats["body_statuses"], stats["writes"] = dict(bodies), dict(writes)
            except Exception as error:
                stats["errors"].append(
                    {"stage": "listing", "code": type(error).__name__}
                )
            finally:
                if client is not None:
                    try:
                        client.close()
                    except Exception as error:
                        stats["errors"].append(
                            {"stage": "cleanup", "code": type(error).__name__}
                        )
            if stats["errors"] or stats["parsed"] == 0:
                stats["status"] = "partial" if stats["parsed"] else "failed"
                report["ok"] = False
            stats["body_complete"] = (
                stats["parsed"] > 0
                and stats["body_statuses"].get("text", 0) == stats["parsed"]
            )
        if store is not None:
            store.export_jsonl(output / "jobs.jsonl")
    report["finished_at"] = timestamp()
    report["body_complete"] = all(
        stats["body_complete"] for stats in report["sources"].values()
    )
    write_report(output / "report.json", report)
    return report
