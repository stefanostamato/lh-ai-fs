// Centralized API client. Frontend talks to the backend through these
// helpers - components don't call `fetch` directly.

const API_BASE = 'http://localhost:8002'

export async function analyze() {
  const response = await fetch(`${API_BASE}/analyze`, { method: 'POST' })
  if (!response.ok) {
    throw new Error(`Server responded with ${response.status}`)
  }
  return response.json()
}

export function documentUrl(documentId) {
  return `${API_BASE}/documents/${encodeURIComponent(documentId)}`
}
