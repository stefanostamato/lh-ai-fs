import asyncio
import json
from pathlib import Path

import httpx

from agents.tools.courtlistener_lookup import CourtListenerLookup
from schemas import CaseLookupResult, ExtractedCitation, TextSpan


FIXTURES = Path(__file__).parent / "fixtures"
BRIEF_ID = "brief-rivera"
CITE_PRIVETTE = "Privette v. Superior Court, 5 Cal.4th 689 (1993)"


def _citation(cite: str = CITE_PRIVETTE, quoted: str | None = None) -> ExtractedCitation:
    return ExtractedCitation(
        cite=cite,
        proposition="hirer of an independent contractor is not liable for on-the-job injuries",
        quoted_language=quoted,
        claim_span=TextSpan(
            document_id=BRIEF_ID,
            start=0,
            end=len(cite),
            excerpt=cite,
        ),
    )


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def _patch_http(monkeypatch, payload):
    """Replace the lookup's _http_get with one that returns `payload`.

    `payload` may be a value (returned from every call) or a callable taking
    (path, params) and returning the value (or raising).
    """

    calls: list[dict] = []

    async def _fake(self, path, params):
        calls.append({"path": path, "params": dict(params)})
        if callable(payload):
            return payload(path, params)
        return payload

    monkeypatch.setattr(
        CourtListenerLookup, "_http_get", _fake, raising=True
    )
    return calls


def test_unparsable_cite_returns_not_found_without_network(monkeypatch):
    async def _boom(self, path, params):
        raise RuntimeError("network must not be touched for unparsable cites")

    monkeypatch.setattr(CourtListenerLookup, "_http_get", _boom, raising=True)

    result = asyncio.run(
        CourtListenerLookup().fetch(_citation(cite="Smith v. Jones"))
    )

    assert isinstance(result, CaseLookupResult)
    assert result.lookup_status == "not_found"
    assert result.found is False
    assert result.holding_text is None
    assert result.source_url is None


def test_single_cluster_with_headnote_returns_found(monkeypatch):
    fixture = _load_fixture("courtlistener_privette.json")
    expected_headnote = fixture["results"][0]["headnote"]

    _patch_http(monkeypatch, fixture)

    result = asyncio.run(CourtListenerLookup().fetch(_citation()))

    assert result.lookup_status == "found"
    assert result.found is True
    assert result.holding_text == expected_headnote
    assert result.source_url is not None
    assert result.source_url.startswith("https://www.courtlistener.com")


def test_quoted_text_match_uses_full_opinion_text(monkeypatch):
    fixture = _load_fixture("courtlistener_privette.json")
    _patch_http(monkeypatch, fixture)

    # Quote that lives in opinion snippet, not in the headnote.
    quoted = "peculiar risk doctrine"
    result = asyncio.run(
        CourtListenerLookup().fetch(_citation(quoted=quoted))
    )

    assert result.lookup_status == "found"
    assert result.quoted_text_match is True


def test_quoted_text_match_is_none_when_citation_has_no_quote(monkeypatch):
    fixture = _load_fixture("courtlistener_privette.json")
    _patch_http(monkeypatch, fixture)

    result = asyncio.run(CourtListenerLookup().fetch(_citation(quoted=None)))

    assert result.quoted_text_match is None


def test_zero_clusters_returns_not_found(monkeypatch):
    _patch_http(monkeypatch, {"count": 0, "results": []})

    result = asyncio.run(CourtListenerLookup().fetch(_citation()))

    assert result.lookup_status == "not_found"
    assert result.found is False
    assert result.holding_text is None
    assert result.source_url is None


def test_two_clusters_returns_ambiguous(monkeypatch):
    fixture = _load_fixture("courtlistener_privette.json")
    cluster = fixture["results"][0]
    payload = {"count": 2, "results": [cluster, cluster]}
    _patch_http(monkeypatch, payload)

    result = asyncio.run(CourtListenerLookup().fetch(_citation()))

    assert result.lookup_status == "ambiguous"
    assert result.holding_text is None
    assert result.notes is not None
    assert "2" in result.notes


def test_http_error_returns_lookup_failed(monkeypatch):
    def _raise(path, params):
        raise httpx.ConnectError("no network")

    _patch_http(monkeypatch, _raise)

    result = asyncio.run(CourtListenerLookup().fetch(_citation()))

    assert result.lookup_status == "lookup_failed"
    assert result.found is False
    assert result.holding_text is None


def test_holding_text_falls_back_to_opinion_text(monkeypatch):
    fixture = _load_fixture("courtlistener_privette.json")
    cluster = fixture["results"][0]
    big_text = "x" * 3000
    cluster = {
        **cluster,
        "headnote": "",
        "summary": "",
        "syllabus": "",
        "opinions": [
            {"id": 1, "type": "combined-opinion", "snippet": big_text}
        ],
    }
    _patch_http(monkeypatch, {"count": 1, "results": [cluster]})

    result = asyncio.run(CourtListenerLookup().fetch(_citation()))

    assert result.lookup_status == "found"
    assert result.holding_text is not None
    assert len(result.holding_text) == 2000
    assert result.holding_text == big_text[:2000]


def test_holding_text_prefers_summary_over_syllabus(monkeypatch):
    fixture = _load_fixture("courtlistener_privette.json")
    cluster = {
        **fixture["results"][0],
        "headnote": "",
        "summary": "summary text wins",
        "syllabus": "syllabus loses",
    }
    _patch_http(monkeypatch, {"count": 1, "results": [cluster]})

    result = asyncio.run(CourtListenerLookup().fetch(_citation()))

    assert result.holding_text == "summary text wins"


def test_canonical_cite_and_source_url_populated(monkeypatch):
    fixture = _load_fixture("courtlistener_privette.json")
    _patch_http(monkeypatch, fixture)

    result = asyncio.run(CourtListenerLookup().fetch(_citation()))

    assert result.canonical_cite is not None
    assert "Privette" in result.canonical_cite
    assert result.source_url == (
        "https://www.courtlistener.com"
        + fixture["results"][0]["absolute_url"]
    )


def _privette_cluster() -> dict:
    return _load_fixture("courtlistener_privette.json")["results"][0]


def _decoy_cluster(
    *, citations: list[str], case_name: str, absolute_url: str
) -> dict:
    return {
        "absolute_url": absolute_url,
        "caseName": case_name,
        "citation": citations,
        "cluster_id": 9999,
        "court": "Some Court",
        "headnote": "decoy headnote",
        "summary": "",
        "syllabus": "",
        "opinions": [
            {"id": 9999, "type": "combined-opinion", "snippet": "decoy"}
        ],
    }


def test_search_filters_to_exact_cite_match_resolves_found(monkeypatch):
    """v4 search returns 3 near-miss results; only one has the parsed cite."""
    privette = _privette_cluster()
    decoy_a = _decoy_cluster(
        citations=["50 Cal. 4th 689", "100 P.3d 1"],
        case_name="Decoy A",
        absolute_url="/opinion/1/decoy-a/",
    )
    decoy_b = _decoy_cluster(
        citations=["5 Cal. 4th 6890"],
        case_name="Decoy B",
        absolute_url="/opinion/2/decoy-b/",
    )
    payload = {"count": 3, "results": [decoy_a, privette, decoy_b]}
    _patch_http(monkeypatch, payload)

    result = asyncio.run(CourtListenerLookup().fetch(_citation()))

    assert result.lookup_status == "found"
    assert result.found is True
    assert result.holding_text == privette["headnote"]
    assert result.source_url == (
        "https://www.courtlistener.com" + privette["absolute_url"]
    )


def test_search_with_no_exact_cite_match_resolves_not_found(monkeypatch):
    """All 3 results are near-misses; none contain the parsed cite exactly."""
    decoy_a = _decoy_cluster(
        citations=["50 Cal. 4th 689"],
        case_name="Decoy A",
        absolute_url="/opinion/1/decoy-a/",
    )
    decoy_b = _decoy_cluster(
        citations=["5 Cal. 4th 6890"],
        case_name="Decoy B",
        absolute_url="/opinion/2/decoy-b/",
    )
    decoy_c = _decoy_cluster(
        citations=["6 Cal. 4th 689"],
        case_name="Decoy C",
        absolute_url="/opinion/3/decoy-c/",
    )
    payload = {"count": 3, "results": [decoy_a, decoy_b, decoy_c]}
    _patch_http(monkeypatch, payload)

    result = asyncio.run(CourtListenerLookup().fetch(_citation()))

    assert result.lookup_status == "not_found"
    assert result.found is False
    assert result.holding_text is None
    assert result.source_url is None


def test_search_with_multiple_exact_matches_resolves_ambiguous(monkeypatch):
    """Two of three results match the cite exactly -> ambiguous, count of 2."""
    privette_a = _privette_cluster()
    privette_b = {
        **_privette_cluster(),
        "absolute_url": "/opinion/1447864-dup/privette-dup/",
        "cluster_id": 1447865,
    }
    decoy = _decoy_cluster(
        citations=["5 Cal. 4th 6890"],
        case_name="Decoy",
        absolute_url="/opinion/3/decoy/",
    )
    payload = {"count": 3, "results": [privette_a, decoy, privette_b]}
    _patch_http(monkeypatch, payload)

    result = asyncio.run(CourtListenerLookup().fetch(_citation()))

    assert result.lookup_status == "ambiguous"
    assert result.found is False
    assert result.holding_text is None
    assert result.notes is not None
    # Notes should reference the count of matching clusters (2), not total (3).
    assert "2" in result.notes
    assert "3" not in result.notes


