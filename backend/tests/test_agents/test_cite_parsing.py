from agents.tools.cite_parsing import ParsedCite, parse_cite


PRIVETTE = "Privette v. Superior Court, 5 Cal.4th 689 (1993)"


def test_parse_privette_returns_parsed_cite():
    parsed = parse_cite(PRIVETTE)

    assert parsed is not None
    assert isinstance(parsed, ParsedCite)
    assert parsed.volume == 5
    assert parsed.reporter == "Cal.4th"
    assert parsed.page == 689
    assert parsed.year == 1993
    assert parsed.raw == PRIVETTE


def test_parse_no_reporter_returns_none():
    assert parse_cite("Smith v. Jones") is None


def test_parsed_cite_is_frozen():
    parsed = parse_cite(PRIVETTE)
    assert parsed is not None
    try:
        parsed.volume = 99
    except Exception:
        return
    raise AssertionError("ParsedCite should be frozen")


def test_parse_handles_fabricated_but_well_formed_cite():
    parsed = parse_cite("Whitmore v. Delgado Scaffolding Co., 334 F. Supp. 2d 1189")

    assert parsed is not None
    assert parsed.volume == 334
    assert parsed.page == 1189
    assert parsed.reporter == "F. Supp. 2d"
    assert parsed.year is None
