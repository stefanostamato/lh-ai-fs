import re

from schemas import (
    DocumentInput,
    DocumentRole,
    ParagraphSpan,
    ParsedRecord,
    TextSpan,
)


_PARAGRAPH_SEPARATOR = re.compile(r"\n\n+")


def parse_record(doc: DocumentInput) -> ParsedRecord:
    if doc.role != DocumentRole.RECORD:
        raise ValueError(
            f"parse_record expects role={DocumentRole.RECORD.value!r}, got {doc.role.value!r}"
        )

    paragraphs: list[ParagraphSpan] = []
    cursor = 0
    paragraph_index = 0
    for chunk in _PARAGRAPH_SEPARATOR.split(doc.text):
        chunk_start = cursor
        cursor += len(chunk)
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

    return ParsedRecord(
        document_id=doc.document_id,
        display_name=doc.display_name,
        raw_text=doc.text,
        paragraphs=paragraphs,
    )
