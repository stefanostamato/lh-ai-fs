import asyncio
import json
import re
from typing import Any

import httpx

from agents.tools.cite_parsing import ParsedCite, parse_cite
from agents.tools.quote_match import quote_appears_in
from schemas import CaseLookupResult, ExtractedCitation


SEARCH_PATH = "/api/rest/v4/search/"
HOLDING_MAX_CHARS = 2000


class CourtListenerLookup:
    """Real case-law lookup against CourtListener's free API.

    The verifier sees a `CaseLookupResult` regardless of whether the source is
    CourtListener, parametric memory, or anything else. This class is the
    network seam: cite parsing in, structured result out, no prompts, no LLM.
    """

    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        base_url: str = "https://www.courtlistener.com",
        timeout_s: float = 15.0,
    ) -> None:
        self._client = client
        self._base_url = base_url
        self._timeout_s = timeout_s

    async def fetch(self, citation: ExtractedCitation) -> CaseLookupResult:
        parsed = parse_cite(citation.cite)
        if parsed is None:
            return _result(
                lookup_status="not_found",
                notes="cite did not parse",
            )

        try:
            payload = await self._http_get(
                SEARCH_PATH, _query_params(parsed)
            )
        except (httpx.HTTPError, asyncio.TimeoutError, json.JSONDecodeError):
            return _result(lookup_status="lookup_failed")

        clusters = _clusters_from_payload(payload)
        # v4 /search is relevance-ranked, not a citation lookup, so the result
        # set can include near-misses (e.g. "5 Cal. 4th 6890" when we asked
        # for "5 Cal.4th 689"). Filter to exact-cite matches before the
        # 0/1/>1 dispatch so the plan's "1 matching cluster -> found"
        # semantics still hold on top of search.
        clusters = _filter_clusters_by_cite(clusters, parsed)

        if not clusters:
            return _result(lookup_status="not_found")

        if len(clusters) > 1:
            return _result(
                lookup_status="ambiguous",
                notes=f"{len(clusters)} clusters matched",
            )

        cluster = clusters[0]
        opinion_text = _full_opinion_text(cluster)
        holding_text = _holding_text(cluster, opinion_text)
        source_url = _source_url(cluster, self._base_url)
        canonical_cite = _canonical_cite(cluster)
        quoted_text_match = (
            quote_appears_in(citation.quoted_language, opinion_text)
            if citation.quoted_language
            else None
        )

        return CaseLookupResult(
            found=True,
            canonical_cite=canonical_cite,
            holding_text=holding_text,
            quoted_text_match=quoted_text_match,
            source_url=source_url,
            lookup_status="found",
            notes=None,
        )

    async def _http_get(
        self, path: str, params: dict[str, str | int]
    ) -> dict | list:
        if self._client is not None:
            response = await self._client.get(path, params=params)
            response.raise_for_status()
            return response.json()
        async with httpx.AsyncClient(
            base_url=self._base_url, timeout=self._timeout_s
        ) as client:
            response = await client.get(path, params=params)
            response.raise_for_status()
            return response.json()


def _query_params(parsed: ParsedCite) -> dict[str, str | int]:
    params: dict[str, str | int] = {
        "type": "o",
        "citation": f"{parsed.volume} {parsed.reporter} {parsed.page}",
    }
    return params


def _normalize_cite(value: str) -> str:
    """Normalize a cite for lenient string comparison.

    eyecite emits reporters like "Cal.4th" (no space) while CourtListener
    stores "Cal. 4th" (with a space after the period). Collapsing internal
    whitespace and then dropping whitespace immediately after a period makes
    those equivalent without over-matching: equality on normalized strings
    still distinguishes "5 Cal. 4th 689" from "5 Cal. 4th 6890" (page
    digits are not separated by periods).
    """
    collapsed = re.sub(r"\s+", " ", value).strip().casefold()
    return re.sub(r"\.\s+", ".", collapsed)


def _filter_clusters_by_cite(
    clusters: list[dict[str, Any]], parsed: ParsedCite
) -> list[dict[str, Any]]:
    target = _normalize_cite(
        f"{parsed.volume} {parsed.reporter} {parsed.page}"
    )
    matched: list[dict[str, Any]] = []
    for cluster in clusters:
        if not isinstance(cluster, dict):
            continue
        citations = cluster.get("citation")
        if not isinstance(citations, list):
            continue
        for entry in citations:
            if isinstance(entry, str) and _normalize_cite(entry) == target:
                matched.append(cluster)
                break
    return matched


def _clusters_from_payload(payload: dict | list) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        results = payload.get("results")
        if isinstance(results, list):
            return results
    return []


def _holding_text(cluster: dict[str, Any], opinion_text: str) -> str | None:
    for key in ("headnote", "summary", "syllabus"):
        value = cluster.get(key)
        if isinstance(value, str) and value.strip():
            return value.rstrip()
    if opinion_text.strip():
        return opinion_text[:HOLDING_MAX_CHARS].rstrip()
    return None


def _full_opinion_text(cluster: dict[str, Any]) -> str:
    opinions = cluster.get("sub_opinions") or cluster.get("opinions") or []
    parts: list[str] = []
    for opinion in opinions:
        if not isinstance(opinion, dict):
            continue
        text = opinion.get("plain_text") or opinion.get("snippet") or ""
        if text:
            parts.append(text)
    return "\n".join(parts)


def _source_url(cluster: dict[str, Any], base_url: str) -> str | None:
    path = cluster.get("absolute_url")
    if not isinstance(path, str) or not path:
        return None
    return f"{base_url}{path}"


def _canonical_cite(cluster: dict[str, Any]) -> str | None:
    explicit = cluster.get("citation_string")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()
    case_name = cluster.get("caseName")
    citations = cluster.get("citation")
    first_cite = (
        citations[0]
        if isinstance(citations, list) and citations and isinstance(citations[0], str)
        else None
    )
    if isinstance(case_name, str) and case_name and first_cite:
        return f"{case_name}, {first_cite}"
    if isinstance(case_name, str) and case_name:
        return case_name
    return first_cite


def _result(
    *,
    lookup_status: str,
    notes: str | None = None,
) -> CaseLookupResult:
    return CaseLookupResult(
        found=False,
        canonical_cite=None,
        holding_text=None,
        quoted_text_match=None,
        source_url=None,
        lookup_status=lookup_status,
        notes=notes,
    )
