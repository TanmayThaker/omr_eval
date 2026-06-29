import { useEffect, useMemo, useState } from 'react'
import * as api from './api'
import UploadPanel from './components/UploadPanel'
import ImageViewer from './components/ImageViewer'
import AnswerGrid from './components/AnswerGrid'

export default function App() {
  const [result, setResult] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [view, setView] = useState('overlay')
  const [selectedQ, setSelectedQ] = useState(null)
  const [rollDraft, setRollDraft] = useState('')
  const [pending, setPending] = useState({})   // question -> answer (unsaved)
  const [saving, setSaving] = useState(false)

  useEffect(() => { if (result) setRollDraft(result.roll_number) }, [result?.session_id])

  // Merge unsaved edits into the displayed answers.
  const answers = useMemo(() => {
    if (!result) return []
    return result.answers.map((a) =>
      pending[a.question] != null
        ? { ...a, answer: pending[a.question], corrected: true }
        : a)
  }, [result, pending])

  const counts = useMemo(() => {
    const c = { total: answers.length, marked: 0, blank: 0, multi: 0, low: 0 }
    for (const a of answers) {
      if (a.answer === 'BLANK') c.blank++
      else if (a.answer === 'MULTI') c.multi++
      else {
        c.marked++
        if (a.confidence < 0.5 && !a.corrected) c.low++
      }
    }
    return c
  }, [answers])

  const dirty = Object.keys(pending).length > 0 ||
    (result && rollDraft !== result.roll_number)

  async function handleFile(file) {
    setLoading(true); setError(null); setSelectedQ(null); setPending({})
    try {
      const r = await api.extract(file)
      setResult(r)
      setView('overlay')
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  function editAnswer(q, val) {
    setPending((p) => {
      const next = { ...p }
      const orig = result.answers.find((a) => a.question === q)?.answer
      if (val === orig) delete next[q]
      else next[q] = val
      return next
    })
  }

  async function save() {
    setSaving(true)
    try {
      const corrections = Object.entries(pending).map(([q, answer]) =>
        ({ question: Number(q), answer }))
      const r = await api.saveCorrections(result.session_id,
        { roll_number: rollDraft, corrections })
      setResult(r); setPending({})
    } catch (e) {
      setError(e.message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">📝 OMR Evaluation</div>
        {result && (
          <>
            <label className="roll">
              Roll #
              <input value={rollDraft} onChange={(e) => setRollDraft(e.target.value)} />
              <span className="conf-pill" title="detection confidence">
                {Math.round(result.roll_confidence * 100)}%
              </span>
            </label>
            <div className="badges">
              <span className="badge ok">{counts.marked} marked</span>
              <span className="badge bad">{counts.blank} blank</span>
              <span className="badge bad">{counts.multi} multi</span>
              {counts.low > 0 && <span className="badge low">{counts.low} low-conf</span>}
            </div>
            <div className="spacer" />
            <div className="view-toggle">
              <button className={view === 'overlay' ? 'on' : ''} onClick={() => setView('overlay')}>Overlay</button>
              <button className={view === 'original' ? 'on' : ''} onClick={() => setView('original')}>Original</button>
            </div>
            <button className="btn primary" disabled={!dirty || saving} onClick={save}>
              {saving ? 'Saving…' : dirty ? 'Save corrections' : 'Saved'}
            </button>
            <a className="btn" href={api.csvUrl(result.session_id)}>CSV</a>
            <a className="btn" href={api.jsonUrl(result.session_id)}>JSON</a>
            <button className="btn ghost" onClick={() => { setResult(null); setError(null) }}>New</button>
          </>
        )}
      </header>

      {result && (result.orientation !== '0' || result.inverted ||
                  Math.abs(result.skew_applied_deg) >= 0.25 ||
                  (result.resolution_scale && result.resolution_scale !== 1)) && (
        <div className="infobar">
          🛠 Auto-corrected:
          {result.orientation !== '0' && <span> orientation ({result.orientation})</span>}
          {result.inverted && <span> · inverted colors</span>}
          {Math.abs(result.skew_applied_deg) >= 0.25 &&
            <span> · skew {result.skew_applied_deg}°</span>}
          {result.resolution_scale !== 1 &&
            <span> · rescaled ×{result.resolution_scale}</span>}
        </div>
      )}
      {result?.warnings?.length > 0 && (
        <div className="warnbar">⚠ {result.warnings.join(' · ')}</div>
      )}
      {error && <div className="errorbar">⚠ {error}</div>}

      {!result ? (
        <div className="center"><UploadPanel onFile={handleFile} loading={loading} /></div>
      ) : (
        <main className="workspace">
          <section className="left">
            <ImageViewer result={{ ...result, answers }} view={view} selectedQ={selectedQ} />
          </section>
          <section className="right">
            <AnswerGrid answers={answers} selectedQ={selectedQ}
                        onSelect={setSelectedQ} onChange={editAnswer} />
          </section>
        </main>
      )}
    </div>
  )
}
