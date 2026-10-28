"""Versioned, provider-neutral records consumed by storage and future clients."""

from dataclasses import asdict, dataclass
import hashlib
import json
from typing import Protocol


@dataclass(frozen=True)
class Job:
    source: str
    source_id: str
    url: str
    title: str
    observed_at: str
    company: str | None = None
    location: str | None = None
    industry: str | None = None
    salary_amount: int | None = None
    salary_unit: str | None = None
    salary_text: str | None = None
    work_days: str | None = None
    work_hours: str | None = None
    work_period: str | None = None
    posted_at: str | None = None
    deadline: str | None = None
    description: str | None = None
    body_text: str | None = None
    body_status: str = "not_requested"
    schema_version: int = 1

    def to_dict(self) -> dict:
        return asdict(self)

    def content_hash(self) -> str:
        content = self.to_dict()
        content.pop("observed_at")
        return hashlib.sha256(
            json.dumps(content, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()


class JobSink(Protocol):
    """A cloud adapter can implement this idempotent, source-scoped upsert."""

    def save(self, job: Job) -> str:
        """Return inserted, updated, unchanged, or stale; raise on write failure."""
        ...
