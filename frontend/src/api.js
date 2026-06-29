// Thin API client. Same-origin in production; Vite proxy handles /api in dev.
const BASE = ''

// Resolved once on first call; shared across all subsequent requests.
let _keyPromise = null
function getApiKey() {
  if (!_keyPromise) {
    _keyPromise = fetch(`${BASE}/api/config`)
      .then(r => r.json())
      .then(d => d.apiKey || '')
      .catch(() => '')
  }
  return _keyPromise
}

async function apiHeaders(extra = {}) {
  const key = await getApiKey()
  return key ? { 'X-API-Key': key, ...extra } : extra
}

export async function extract(file) {
  const fd = new FormData()
  fd.append('file', file)
  const r = await fetch(`${BASE}/api/extract`, {
    method: 'POST',
    headers: await apiHeaders(),
    body: fd,
  })
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `Extract failed (${r.status})`)
  return r.json()
}

export async function saveCorrections(sessionId, { roll_number, series, corrections }) {
  const r = await fetch(`${BASE}/api/correct/${sessionId}`, {
    method: 'POST',
    headers: await apiHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify({ roll_number, series, corrections }),
  })
  if (!r.ok) throw new Error(`Save failed (${r.status})`)
  return r.json()
}

export const scanUrl = (id) => `${BASE}/api/scan/${id}`
export const overlayUrl = (id) => `${BASE}/api/overlay/${id}`
export const csvUrl = (id) => `${BASE}/api/result/${id}/csv`
export const jsonUrl = (id) => `${BASE}/api/result/${id}/json`
