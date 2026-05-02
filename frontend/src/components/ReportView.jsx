import FlagCard from './FlagCard'
import MemoSection from './MemoSection'
import './styles.css'

// Top-level rendering of a Report. Builds documentsById once from
// report.meta.documents and threads it down so the card never has to
// know which case is loaded.

function buildDocumentsById(documents) {
  const map = {}
  if (!Array.isArray(documents)) return map
  for (const d of documents) {
    map[d.document_id] = d
  }
  return map
}

function ReportView({ report }) {
  if (!report) return null

  const meta = report.meta || {}
  const documentsById = buildDocumentsById(meta.documents)
  const consistency = Array.isArray(report.consistency) ? report.consistency : []
  const citations = Array.isArray(report.citations) ? report.citations : []
  const tokens = meta.token_usage || { prompt: 0, completion: 0 }
  const totalTokens = (tokens.prompt || 0) + (tokens.completion || 0)

  return (
    <div className="report">
      <MemoSection memo={report.memo} />

      <section>
        <h2 className="section-title">Cross-document consistency</h2>
        {consistency.length === 0 ? (
          <p className="section-empty">No consistency findings.</p>
        ) : (
          consistency.map((f, i) => (
            <FlagCard
              key={`consistency-${i}`}
              finding={f}
              type="consistency"
              documentsById={documentsById}
            />
          ))
        )}
      </section>

      <section>
        <h2 className="section-title">Citations</h2>
        {citations.length === 0 ? (
          <p className="section-empty">No citation findings.</p>
        ) : (
          citations.map((f, i) => (
            <FlagCard
              key={`citation-${i}`}
              finding={f}
              type="citation"
              documentsById={documentsById}
            />
          ))
        )}
      </section>

      <footer className="report-footer">
        <span>model: {meta.model || 'unknown'}</span>
        <span>elapsed: {meta.elapsed_ms ?? 0} ms</span>
        <span>tokens: {totalTokens}</span>
        {Array.isArray(meta.partial_failures) && meta.partial_failures.length > 0 && (
          <span>partial failures: {meta.partial_failures.length}</span>
        )}
      </footer>
    </div>
  )
}

export default ReportView
