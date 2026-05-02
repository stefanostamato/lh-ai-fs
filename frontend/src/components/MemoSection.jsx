// Renders the judicial memo. Paragraph + a small "what's in here" caption.

function MemoSection({ memo }) {
  if (!memo) return null

  const topCount = Array.isArray(memo.top_findings) ? memo.top_findings.length : 0

  return (
    <section className="memo">
      <h2 className="section-title">Judicial memo</h2>
      <p className="memo-text">{memo.text}</p>
      <p className="memo-caption">
        Highlights {topCount} top finding{topCount === 1 ? '' : 's'}.
      </p>
      {memo.partial_failure_note && (
        <p className="memo-partial-failure">{memo.partial_failure_note}</p>
      )}
    </section>
  )
}

export default MemoSection
