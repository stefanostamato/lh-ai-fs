import { useState } from 'react'
import { documentUrl } from '../api/analyze'

// One card per finding. Used by both consistency and citation findings.
// `documentsById` is the map from the report's meta.documents - the card
// uses it to render display names from a span's document_id without ever
// hardcoding a specific document.

function spanLabel(span, documentsById) {
  if (!span) return null
  const doc = documentsById[span.document_id]
  const name = doc ? doc.display_name : span.document_id
  return `${name} [chars ${span.start}-${span.end}]`
}

function VerdictPill({ verdict }) {
  return (
    <span className={`verdict-pill verdict-${verdict}`}>
      {verdict.replace(/_/g, ' ')}
    </span>
  )
}

function ConfidenceBadge({ value }) {
  const v = typeof value === 'number' ? value.toFixed(2) : '0.00'
  return <span className="confidence-badge">conf {v}</span>
}

function SpanRefButton({ span, documentsById, expanded, onToggle }) {
  if (!span) return null
  return (
    <button type="button" className="span-ref-button" onClick={onToggle}>
      {spanLabel(span, documentsById)} {expanded ? '(hide)' : '(show)'}
    </button>
  )
}

function FullDocLink({ documentId }) {
  if (!documentId) return null
  return (
    <a
      className="full-doc-link"
      href={documentUrl(documentId)}
      target="_blank"
      rel="noreferrer"
    >
      View full document
    </a>
  )
}

function FlagCard({ finding, type, documentsById }) {
  const [showClaim, setShowClaim] = useState(false)
  const [showEvidence, setShowEvidence] = useState(false)

  const claimSpan =
    type === 'citation' ? finding.citation?.claim_span : finding.claim?.claim_span
  const claimText =
    type === 'citation' ? finding.citation?.proposition : finding.claim?.claim_text
  const evidenceSpan = finding.evidence_span
  const evidenceQuote = finding.evidence_quote

  return (
    <div className="flag-card">
      <div className="flag-card-header">
        <VerdictPill verdict={finding.verdict} />
        <ConfidenceBadge value={finding.confidence} />
        {type === 'citation' && finding.citation?.cite && (
          <span className="lookup-info">{finding.citation.cite}</span>
        )}
      </div>

      <p className="flag-claim">
        <span className="flag-claim-label">Claim</span>
        {claimText}
      </p>

      <div className="flag-meta-row">
        <span className="flag-claim-label">Source</span>
        <SpanRefButton
          span={claimSpan}
          documentsById={documentsById}
          expanded={showClaim}
          onToggle={() => setShowClaim((v) => !v)}
        />
        <FullDocLink documentId={claimSpan?.document_id} />
      </div>
      {showClaim && claimSpan && (
        <div className="excerpt">{claimSpan.excerpt}</div>
      )}

      {type === 'citation' && finding.lookup && (
        <div className="lookup-info">
          Lookup status:{' '}
          <span className="lookup-status">{finding.lookup.lookup_status}</span>
          {finding.lookup.canonical_cite &&
            ` - ${finding.lookup.canonical_cite}`}
          {finding.lookup.holding_text && (
            <div className="excerpt">{finding.lookup.holding_text}</div>
          )}
        </div>
      )}

      {type === 'consistency' &&
        Array.isArray(finding.checked_documents) &&
        finding.checked_documents.length > 0 && (
          <div className="flag-meta-row">
            <span className="flag-claim-label">Checked</span>
            {finding.checked_documents
              .map((id) => documentsById[id]?.display_name || id)
              .join(', ')}
          </div>
        )}

      {evidenceSpan && (
        <div className="flag-meta-row">
          <span className="flag-claim-label">Evidence</span>
          <SpanRefButton
            span={evidenceSpan}
            documentsById={documentsById}
            expanded={showEvidence}
            onToggle={() => setShowEvidence((v) => !v)}
          />
          <FullDocLink documentId={evidenceSpan.document_id} />
        </div>
      )}
      {showEvidence && evidenceSpan && (
        <div className="excerpt">{evidenceSpan.excerpt}</div>
      )}
      {showEvidence && !evidenceSpan && evidenceQuote && (
        <div className="excerpt">{evidenceQuote}</div>
      )}

      {finding.reasoning && <p className="flag-rationale">{finding.reasoning}</p>}
    </div>
  )
}

export default FlagCard
