from pathlib import Path

from schemas import DocumentInput, DocumentRole, DocumentSet


DOCUMENTS_DIR = Path(__file__).parent / "documents"


# The single place in the codebase that knows the Rivera filenames, their
# display names, and their roles. Swapping cases is a one-file edit.
_DOCUMENT_MAP: dict[str, tuple[str, DocumentRole]] = {
    "motion_for_summary_judgment": ("Motion for Summary Judgment", DocumentRole.BRIEF),
    "police_report": ("Police Report", DocumentRole.RECORD),
    "medical_records_excerpt": ("Medical Records Excerpt", DocumentRole.RECORD),
    "witness_statement": ("Witness Statement", DocumentRole.RECORD),
}


def load_default_case() -> DocumentSet:
    """Read the four `.txt` files from `backend/documents/` into a DocumentSet."""

    documents: list[DocumentInput] = []
    for stem, (display_name, role) in _DOCUMENT_MAP.items():
        path = DOCUMENTS_DIR / f"{stem}.txt"
        documents.append(
            DocumentInput(
                document_id=stem,
                display_name=display_name,
                role=role,
                text=path.read_text(),
            )
        )
    return DocumentSet(documents=documents)
