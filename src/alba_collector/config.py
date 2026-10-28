from dataclasses import dataclass, fields
from pathlib import Path
import tomllib

from .model import Job


def normalized_region(address: str) -> str:
    aliases = {
        "서울특별시": "서울",
        "서울시": "서울",
        "경기도": "경기",
        "부산광역시": "부산",
        "대구광역시": "대구",
        "인천광역시": "인천",
        "광주광역시": "광주",
        "대전광역시": "대전",
        "울산광역시": "울산",
        "세종특별자치시": "세종",
        "강원특별자치도": "강원",
        "강원도": "강원",
        "충청북도": "충북",
        "충청남도": "충남",
        "전북특별자치도": "전북",
        "전라북도": "전북",
        "전라남도": "전남",
        "경상북도": "경북",
        "경상남도": "경남",
        "제주특별자치도": "제주",
    }
    parts = address.strip().removeprefix("대한민국 ").split()
    if parts:
        parts[0] = aliases.get(parts[0], parts[0])
    return " ".join(parts)


@dataclass(frozen=True)
class Config:
    regions: tuple[str, ...] = ("서울", "경기")
    industries: tuple[str, ...] = ()
    keywords: tuple[str, ...] = ()
    exclude_keywords: tuple[str, ...] = ()
    sources: tuple[str, ...] = ("albamon", "alba")
    pages_per_source: int = 2
    max_details_per_source: int = 10
    request_delay_seconds: float = 2
    timeout_seconds: int = 30
    collect_body: bool = True

    def matches(self, job: Job) -> bool:
        if self.regions and job.location:
            address = normalized_region(job.location)
            if not any(
                address == normalized_region(region)
                or address.startswith(normalized_region(region) + " ")
                for region in self.regions
            ):
                return False
        if (
            self.industries
            and job.industry
            and not any(
                word.casefold() in job.industry.casefold() for word in self.industries
            )
        ):
            return False
        text = " ".join(
            value
            for value in (
                job.title,
                job.company,
                job.industry,
                job.description,
                job.body_text,
            )
            if value
        ).casefold()
        if self.keywords and not any(word.casefold() in text for word in self.keywords):
            return False
        return not any(word.casefold() in text for word in self.exclude_keywords)


def load_config(path: Path) -> Config:
    """Read a strict TOML config; reject typos and invalid limits before I/O."""
    with path.open("rb") as file:
        data = tomllib.load(file)
    known = {field.name for field in fields(Config)}
    if unknown := set(data) - known:
        raise ValueError(f"{path}: unknown keys: {', '.join(sorted(unknown))}")
    for name in ("regions", "industries", "keywords", "exclude_keywords", "sources"):
        if name not in data:
            continue
        value = data[name]
        if not isinstance(value, list) or any(
            not isinstance(v, str) or not v.strip() or v != v.strip() for v in value
        ):
            raise ValueError(f"{path}: {name}: expected nonempty, trimmed strings")
        if len(set(value)) != len(value):
            raise ValueError(f"{path}: {name}: duplicate values")
        data[name] = tuple(value)
    sources = data.get("sources", Config.sources)
    if not sources or set(sources) - {"alba", "albamon"}:
        raise ValueError(f"{path}: sources: expected alba and/or albamon")
    for name, maximum in (
        ("pages_per_source", 100),
        ("max_details_per_source", 1000),
        ("timeout_seconds", 120),
    ):
        value = data.get(name, getattr(Config, name))
        if type(value) is not int or not 1 <= value <= maximum:
            raise ValueError(f"{path}: {name}: expected integer in 1..{maximum}")
    delay = data.get("request_delay_seconds", Config.request_delay_seconds)
    if type(delay) not in (int, float) or not 0.5 <= delay <= 60:
        raise ValueError(f"{path}: request_delay_seconds: expected number in 0.5..60")
    if type(data.get("collect_body", True)) is not bool:
        raise ValueError(f"{path}: collect_body: expected boolean")
    return Config(**data)
