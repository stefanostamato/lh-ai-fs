import re


_SINGLE_QUOTES = "‘’‚‛′"
_DOUBLE_QUOTES = "“”„‟″"
_DASHES = "–—−"

_TRANSLATION = str.maketrans(
    {ch: "'" for ch in _SINGLE_QUOTES}
    | {ch: '"' for ch in _DOUBLE_QUOTES}
    | {ch: "-" for ch in _DASHES}
)

_WHITESPACE = re.compile(r"\s+")


def normalize(text: str) -> str:
    return _WHITESPACE.sub(" ", text.lower().translate(_TRANSLATION)).strip()


def quote_appears_in(quote: str, opinion_text: str) -> bool:
    if not quote:
        return False
    return normalize(quote) in normalize(opinion_text)
