// Thin API client. Same-origin in production; Vite proxy handles /api in dev.
// The server trusts same-origin requests, so the UI sends no API key — the key
// guards only cross-origin/programmatic callers and never reaches the browser.
const BASE = ''

export async function extract(file) {
  const fd = new FormData()
  fd.append('file', file)
  const r = await fetch(`${BASE}/api/extract`, {
    method: 'POST',
    body: fd,
  })
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `Extract failed (${r.status})`)
  return r.json()
}

export async function saveCorrections(sessionId, { roll_number, series, corrections }) {
  const r = await fetch(`${BASE}/api/correct/${sessionId}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ roll_number, series, corrections }),
  })
  if (!r.ok) throw new Error(`Save failed (${r.status})`)
  return r.json()
}

export const scanUrl = (id) => `${BASE}/api/scan/${id}`
export const overlayUrl = (id) => `${BASE}/api/overlay/${id}`
export const csvUrl = (id) => `${BASE}/api/result/${id}/csv`
export const jsonUrl = (id) => `${BASE}/api/result/${id}/json`
