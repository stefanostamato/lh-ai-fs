from agents.tools.quote_match import normalize, quote_appears_in


def test_quote_appears_simple_substring():
    assert quote_appears_in(
        "A hirer is never liable",
        "...A hirer is never liable for...",
    )


def test_quote_appears_with_smart_quotes():
    assert quote_appears_in(
        "a hirer’s general control",
        "A hirer's general control suffices",
    )


def test_quote_absent_returns_false():
    assert not quote_appears_in("totally absent text", "the actual opinion")


def test_empty_quote_returns_false():
    assert not quote_appears_in("", "anything in here")


def test_quote_match_collapses_whitespace_and_newlines():
    quote = "the duty of care\nrests with the hirer"
    opinion = "...we hold that the duty of care rests with the hirer of an independent contractor..."
    assert quote_appears_in(quote, opinion)


def test_quote_match_handles_em_and_en_dashes():
    assert quote_appears_in(
        "the well-settled rule — long established",
        "The well-settled rule - long established - controls here.",
    )


def test_normalize_lowercases_and_strips():
    assert normalize("  Hello   World  ") == "hello world"


def test_normalize_replaces_smart_quotes_and_dashes():
    raw = "“Test” — a ‘case’"
    assert normalize(raw) == '"test" - a \'case\''
