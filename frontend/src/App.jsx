import { useEffect, useRef, useState } from 'react'
import { analyze } from './api/analyze'
import ReportView from './components/ReportView'
import './components/styles.css'

function App() {
  const [report, setReport] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [elapsedMs, setElapsedMs] = useState(0)
  const startedAtRef = useRef(null)
  const tickRef = useRef(null)

  useEffect(() => {
    if (!loading) {
      if (tickRef.current) {
        clearInterval(tickRef.current)
        tickRef.current = null
      }
      return
    }
    startedAtRef.current = performance.now()
    setElapsedMs(0)
    tickRef.current = setInterval(() => {
      setElapsedMs(performance.now() - startedAtRef.current)
    }, 100)
    return () => {
      if (tickRef.current) clearInterval(tickRef.current)
    }
  }, [loading])

  const runAnalysis = async () => {
    setLoading(true)
    setError(null)
    setReport(null)

    try {
      const data = await analyze()
      setReport(data)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  const elapsedSeconds = (elapsedMs / 1000).toFixed(1)

  return (
    <div
      style={{
        maxWidth: '900px',
        margin: '40px auto',
        padding: '0 20px',
        fontFamily: 'system-ui, sans-serif',
      }}
    >
      <h1>BS Detector</h1>
      <p>Legal brief verification pipeline</p>

      <button
        onClick={runAnalysis}
        disabled={loading}
        style={{
          padding: '10px 24px',
          fontSize: '16px',
          cursor: loading ? 'not-allowed' : 'pointer',
        }}
      >
        {loading ? 'Analyzing...' : report ? 'Run again' : 'Run Analysis'}
      </button>

      {loading && (
        <div className="spinner-wrap" role="status" aria-live="polite">
          <span className="spinner" aria-hidden="true" />
          <span>Analyzing documents... {elapsedSeconds}s</span>
        </div>
      )}

      {error && !loading && (
        <div className="error-box">
          <strong>Error:</strong> {error}
          <div style={{ marginTop: '10px' }}>
            <button
              onClick={runAnalysis}
              style={{ padding: '6px 14px', fontSize: '14px', cursor: 'pointer' }}
            >
              Try again
            </button>
          </div>
        </div>
      )}

      {report && !loading && <ReportView report={report} />}

      {report === null && !loading && !error && (
        <p style={{ marginTop: '20px', color: '#888' }}>
          Click "Run Analysis" to analyze the case documents.
        </p>
      )}
    </div>
  )
}

export default App
