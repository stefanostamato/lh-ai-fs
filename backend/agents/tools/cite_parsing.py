from dataclasses import dataclass

from eyecite import get_citations
from eyecite.models import FullCaseCitation


@dataclass(frozen=True)
class ParsedCite:
    volume: int
    reporter: str
    page: int
    year: int | None
    raw: str


def parse_cite(cite: str) -> ParsedCite | None:
    for found in get_citations(cite):
        if not isinstance(found, FullCaseCitation):
            continue
        groups = found.groups
        try:
            volume = int(groups["volume"])
            page = int(groups["page"])
        except (KeyError, TypeError, ValueError):
            return None
        reporter = groups.get("reporter")
        if not reporter:
            return None
        raw_year = getattr(found.metadata, "year", None)
        try:
            year = int(raw_year) if raw_year is not None else None
        except (TypeError, ValueError):
            year = None
        return ParsedCite(
            volume=volume,
            reporter=reporter,
            page=page,
            year=year,
            raw=cite,
        )
    return None
