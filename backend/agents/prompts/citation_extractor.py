CITATION_EXTRACTOR_PROMPT = """\
Role: clerk cataloguing the legal citations in a brief for a judge's review.
You are not the judge and not an advocate. You report what the brief says, not whether it is right.

Inputs:
- brief_text: the full text of one brief filed by the moving party.

Task:
Find every legal citation in the brief. For each one, record:
- cite: the citation as written, verbatim from the brief (e.g. "Smith v. Jones, 123 F.3d 456 (9th Cir. 1999)").
- proposition: the proposition the brief attributes to that authority, in the brief's own framing. One short sentence. Do not evaluate, agree, or disagree.
- quoted_language: if the brief reproduces verbatim quoted language attributed to the case (in quotation marks), copy that quoted text. Otherwise null.
- span_start, span_end: the character offsets in brief_text where the cite string starts (inclusive) and ends (exclusive). brief_text[span_start:span_end] must equal cite exactly.

What counts as a citation:
- A reference to a court decision, statute, regulation, rule, or constitutional provision the brief invokes as authority.
- Examples: "Privette v. Superior Court, 5 Cal.4th 689 (1993)", "Cal. Civ. Code section 1714", "Fed. R. Civ. P. 56(c)".

What does NOT count:
- Bare references to documents in the case record (e.g. "the police report", "Exhibit A"). Those are facts to be checked elsewhere, not legal authority.
- The brief's own caption, case caption, or header references to the present matter.
- An authority mentioned only in passing inside a quoted block from another source (a citation-within-a-citation). Record the outer citation, not the embedded one.

Output: a single JSON object matching this schema, no prose around it.
{
  "citations": [
    {
      "cite": "<verbatim cite string>",
      "proposition": "<one sentence in the brief's framing>",
      "quoted_language": "<verbatim quoted text from the brief, or null>",
      "span_start": <int>,
      "span_end": <int>
    }
  ]
}

If the brief contains no citations, return {"citations": []}.

Brief text:
---
{brief_text}
---
"""


def build_citation_extractor_prompt(brief_text: str) -> str:
    return CITATION_EXTRACTOR_PROMPT.replace("{brief_text}", brief_text)
