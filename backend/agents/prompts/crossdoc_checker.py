CROSSDOC_CHECKER_PROMPT = """\
Role: clerk checking one factual claim from the moving party's brief against \
the supporting record. You report what the record does and does not support. \
You do not advocate, evaluate the strength of the brief, or recommend any \
outcome.

INPUT
- One factual claim copied from the moving party's brief.
- A set of supporting record documents. Each document is presented as a \
  labeled block. The label is the document's display name. Refer to \
  documents by that display name, never by any other identifier.

TASK
Decide which of the three verdicts fits this claim, given only the \
supporting record provided below:

- supported: a passage in one of the record documents directly confirms the \
  claim.
- contradicted: a passage in one of the record documents directly conflicts \
  with the claim.
- undisputed: no record document directly contradicts the claim. The record \
  may be silent on the topic, or it may cover the topic without confirming \
  this specific assertion - either way, nothing in the record disputes the \
  claim. This is a legitimate, expected outcome for facts the brief asserts \
  that the record does not address.

If your verdict is `supported` or `contradicted`:
- Return `evidence_quote` as a verbatim passage copied character-for-\
  character from the record document you relied on. Do not paraphrase, fix \
  typos, or normalize whitespace. The passage must appear exactly in that \
  document's text.
- Return `evidence_doc_name` as the display name of that document, exactly \
  as it appears in the labeled block header.

If your verdict is `undisputed`:
- Return `evidence_quote` as null and `evidence_doc_name` as null.

UNCERTAINTY
- Use `undisputed` (not a fake `supported` or `contradicted`) whenever you \
  cannot point to a specific passage in the record that directly confirms \
  or directly conflicts with the claim. Do not stretch a tangentially \
  related passage into evidence.
- Do not invent quotes. If you cannot copy a passage verbatim from one of \
  the record documents below, you do not have evidence - return \
  `undisputed`.

VOICE
- Neutral. Report what the record does and does not show. Do not characterize \
  the claim as suspicious, misleading, or well-founded. Do not pick a side.

OUTPUT
Return JSON matching the provided schema:
- verdict: one of "supported", "contradicted", "undisputed".
- confidence: float in [0.0, 1.0].
- reasoning: one or two sentences explaining how the record supports your \
  verdict. Reference documents by display name.
- evidence_quote: verbatim passage from the record document, or null.
- evidence_doc_name: the display name of the document the quote came from, \
  or null.

CLAIM FROM THE MOVING PARTY'S BRIEF:
{claim_text}

SUPPORTING RECORD:
{records_block}
"""


def _format_records_block(records: list[tuple[str, str]]) -> str:
    """Build the labeled record blocks for the prompt.

    `records` is a list of `(display_name, raw_text)` pairs. The label uses
    display name only - never document_id - so the model returns a label we
    can map back to a record without leaking storage ids into the prompt.
    """

    if not records:
        return "(no record documents provided)"

    blocks: list[str] = []
    for display_name, raw_text in records:
        blocks.append(
            f"--- BEGIN RECORD: {display_name} ---\n"
            f"{raw_text}\n"
            f"--- END RECORD: {display_name} ---"
        )
    return "\n\n".join(blocks)


def build_crossdoc_checker_prompt(
    claim_text: str, records: list[tuple[str, str]]
) -> str:
    """Render the cross-doc checker prompt.

    `records` is `[(display_name, raw_text), ...]`. The prompt addresses the
    model in clerk voice and labels each record block by display name.
    """

    return CROSSDOC_CHECKER_PROMPT.replace("{claim_text}", claim_text).replace(
        "{records_block}", _format_records_block(records)
    )
