---
description: Append a verbose, diary-style entry to NOTES.md capturing thought process, decisions, and dead-ends.
argument-hint: [optional: short topic hint, e.g. "why I picked approach B"]
---

You are writing into [NOTES.md](../../NOTES.md). This is Stefano's running diary for the challenge - raw, honest, verbose. He'll mine it later when writing the final REFLECTION.md, so the goal is to capture things he might forget. Topic hint (if any): $ARGUMENTS

## How to write

- **Talk like a person.** Short sentences. Contractions. No business-speak, no "leverage", no "in order to".
- **Use dashes (-), never emdashes (--).** This is a hard rule across the project.
- **Verbose is fine here.** This isn't user-facing copy. If you have three paragraphs of thinking, write three paragraphs.
- **Honest > flattering.** If something didn't work, say so. If a decision was a coin flip, say so. The reflection at the end is graded on honesty.
- **Mermaid diagrams when they help.** If you're describing a pipeline shape, an agent graph, or a decision tree, draw it instead of describing it in prose.

## What to capture

Pull from the recent conversation and what's actually changed in the repo. A good entry covers some mix of:

- What we worked on this session and what shipped
- What decisions we made and why we made them (especially the tradeoffs)
- What we tried that didn't work, and what we learned from it
- Open questions, things bugging us, things we'd revisit with more time
- Surprises - anything where reality didn't match expectation

Skip the play-by-play of every tool call. Skip "I read file X then file Y". The diary is for thinking, not for narrating.

## How to append

1. Read the existing [NOTES.md](../../NOTES.md) first - match its tone and structure (it has a "Stages" section with subsections like "0. Project setup").
2. Figure out which stage/subsection this entry belongs in. If it fits an existing one, append. If it's a new phase, add a new subsection with the next number.
3. Lead the entry with today's date in `YYYY-MM-DD` format and a short title.
4. Write the entry. Verbose. Conversational. Honest.
5. Don't touch unrelated parts of the file.

## After writing

Print to the user:
- Which section you appended to
- A 2-line summary of what you captured
- Any open questions you noted that he might want to think about

Don't ask permission first - just write. He invoked the command because he wants something written.
