"""SQLite local sink and portable JSONL export. No cloud provider coupling."""

from datetime import datetime
import json
from pathlib import Path
import sqlite3

from .model import Job


class SQLiteStore:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS jobs (
                source TEXT NOT NULL, source_id TEXT NOT NULL, payload TEXT NOT NULL,
                content_hash TEXT NOT NULL, first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
                PRIMARY KEY(source, source_id)
            );
            CREATE TABLE IF NOT EXISTS job_versions (
                id INTEGER PRIMARY KEY, source TEXT NOT NULL, source_id TEXT NOT NULL,
                observed_at TEXT NOT NULL, content_hash TEXT NOT NULL, payload TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS versions_job ON job_versions(source, source_id);
            PRAGMA user_version = 1;
        """)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.connection.close()

    def save(self, job: Job) -> str:
        digest = job.content_hash()
        payload = json.dumps(job.to_dict(), ensure_ascii=False, sort_keys=True)
        with self.connection:
            previous = self.connection.execute(
                "SELECT * FROM jobs WHERE source=? AND source_id=?",
                (job.source, job.source_id),
            ).fetchone()
            if previous and datetime.fromisoformat(
                job.observed_at
            ) < datetime.fromisoformat(previous["last_seen"]):
                return "stale"
            status = (
                "inserted"
                if previous is None
                else ("unchanged" if previous["content_hash"] == digest else "updated")
            )
            self.connection.execute(
                """
                INSERT INTO jobs VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(source, source_id) DO UPDATE SET
                    payload=excluded.payload, content_hash=excluded.content_hash, last_seen=excluded.last_seen
                """,
                (
                    job.source,
                    job.source_id,
                    payload,
                    digest,
                    job.observed_at,
                    job.observed_at,
                ),
            )
            if status != "unchanged":
                self.connection.execute(
                    "INSERT INTO job_versions(source,source_id,observed_at,content_hash,payload) VALUES (?,?,?,?,?)",
                    (job.source, job.source_id, job.observed_at, digest, payload),
                )
        return status

    def query(
        self, *, limit: int = 20, source: str | None = None, keyword: str | None = None
    ) -> list[dict]:
        if type(limit) is not int or not 1 <= limit <= 10000:
            raise ValueError("limit must be an integer in 1..10000")
        where, params = [], []
        if source:
            where.append("source = ?")
            params.append(source)
        if keyword:
            where.append("instr(payload, ?) > 0")
            params.append(keyword)
        sql = (
            "SELECT payload, first_seen, last_seen FROM jobs"
            + (" WHERE " + " AND ".join(where) if where else "")
            + " ORDER BY last_seen DESC, source, source_id LIMIT ?"
        )
        result = []
        for row in self.connection.execute(sql, (*params, limit)):
            result.append(
                {
                    **json.loads(row["payload"]),
                    "first_seen": row["first_seen"],
                    "last_seen": row["last_seen"],
                }
            )
        return result

    def export_jsonl(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        with temporary.open("w", encoding="utf-8") as file:
            for row in self.connection.execute(
                "SELECT payload,first_seen,last_seen FROM jobs ORDER BY source,source_id"
            ):
                file.write(
                    json.dumps(
                        {
                            **json.loads(row["payload"]),
                            "first_seen": row["first_seen"],
                            "last_seen": row["last_seen"],
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
        temporary.replace(path)
