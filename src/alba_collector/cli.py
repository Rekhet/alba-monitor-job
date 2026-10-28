import argparse
from dataclasses import replace
import json
from pathlib import Path
import sys

from .config import load_config
from .runner import collect
from .storage import SQLiteStore


def positive(value: str) -> int:
    number = int(value)
    if not 1 <= number <= 1000:
        raise argparse.ArgumentTypeError("expected integer in 1..1000")
    return number


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="알바몬·알바천국 bounded collector")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("collect", "probe"):
        command = commands.add_parser(name)
        command.add_argument("--config", type=Path, default=Path("config.toml"))
        command.add_argument(
            "--output",
            type=Path,
            default=Path("data" if name == "collect" else "reports"),
        )
        command.add_argument("--source", choices=("albamon", "alba", "all"))
        command.add_argument("--limit", type=positive)
        command.add_argument("--pages", type=positive)
    query = commands.add_parser("query")
    query.add_argument("--db", type=Path, default=Path("data/jobs.sqlite3"))
    query.add_argument("--source", choices=("albamon", "alba"))
    query.add_argument("--keyword")
    query.add_argument("--limit", type=positive, default=20)
    query.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "query":
            if not args.db.is_file():
                raise ValueError(f"database not found: {args.db}")
            with SQLiteStore(args.db) as store:
                rows = store.query(
                    limit=args.limit, source=args.source, keyword=args.keyword
                )
            if args.json:
                print(json.dumps(rows, ensure_ascii=False, indent=2))
            else:
                for row in rows:
                    print(
                        f"[{row['source']}:{row['source_id']}] {row['title']}\n  {row['location'] or '지역 미확인'} | {row['salary_amount'] or '급여 미확인'} {row['salary_unit'] or ''} | 본문: {row['body_status']}\n  {row['url']}"
                    )
            return 0
        config = load_config(args.config)
        changes = {}
        if args.source:
            changes["sources"] = (
                ("albamon", "alba") if args.source == "all" else (args.source,)
            )
        if args.limit:
            changes["max_details_per_source"] = args.limit
        if args.pages:
            if args.pages > 100:
                raise ValueError("--pages: expected integer in 1..100")
            changes["pages_per_source"] = args.pages
        if args.command == "probe":
            changes.setdefault("pages_per_source", 1)
            changes.setdefault("max_details_per_source", 2)
        report = collect(
            replace(config, **changes), args.output, probe=args.command == "probe"
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["ok"] else 1
    except (ValueError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
