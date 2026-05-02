import re

from schemas import (
    DocumentInput,
    DocumentRole,
    ParagraphSpan,
    ParsedBrief,
    TextSpan,
)


_PARAGRAPH_SEPARATOR = re.compile(r"\n\n+")


def parse_brief(doc: DocumentInput) -> ParsedBrief:
    if doc.role != DocumentRole.BRIEF:
        raise ValueError(
            f"parse_brief expects role={DocumentRole.BRIEF.value!r}, got {doc.role.value!r}"
        )

    paragraphs: list[ParagraphSpan] = []
    cursor = 0
    paragraph_index = 0
    for chunk in _PARAGRAPH_SEPARATOR.split(doc.text):
        chunk_start = cursor
        cursor += len(chunk)
        # advance past the separator that re.split consumed
        match = _PARAGRAPH_SEPARATOR.match(doc.text, cursor)
        if match:
            cursor = match.end()
        excerpt = chunk.strip()
        if not excerpt:
            continue
        paragraphs.append(
            ParagraphSpan(
                document_id=doc.document_id,
                paragraph_index=paragraph_index,
                span=TextSpan(
                    document_id=doc.document_id,
                    start=chunk_start,
                    end=chunk_start + len(chunk),
                    excerpt=excerpt,
                ),
            )
        )
        paragraph_index += 1

    return ParsedBrief(
        document_id=doc.document_id,
        raw_text=doc.text,
        paragraphs=paragraphs,
    )
