const OPTIONS = ['A', 'B', 'C', 'D', 'E']
const CHOICES = [...OPTIONS, 'BLANK', 'MULTI']

function statusClass(a) {
  if (a.corrected) return 'corrected'
  if (a.answer === 'BLANK' || a.answer === 'MULTI') return 'bad'
  if (a.confidence < 0.5) return 'low'
  return 'ok'
}

function Row({ a, selected, onSelect, onChange }) {
  return (
    <tr
      className={`row ${statusClass(a)} ${selected ? 'sel' : ''}`}
      onClick={() => onSelect(a.question)}
    >
      <td className="q">{a.question}</td>
      <td className="opts">
        {OPTIONS.map((o) => (
          <span key={o} className={`bub ${a.answer === o ? 'on' : ''}`}>{o}</span>
        ))}
      </td>
      <td className="conf">{a.answer === 'BLANK' || a.answer === 'MULTI' ? a.answer : `${Math.round(a.confidence * 100)}%`}</td>
      <td>
        <select
          value={a.answer}
          onClick={(e) => e.stopPropagation()}
          onChange={(e) => onChange(a.question, e.target.value)}
        >
          {CHOICES.map((c) => <option key={c} value={c}>{c}</option>)}
        </select>
      </td>
    </tr>
  )
}

export default function AnswerGrid({ answers, selectedQ, onSelect, onChange }) {
  // 4 columns of 50 mirroring the sheet layout
  const cols = [0, 1, 2, 3].map((b) => answers.slice(b * 50, b * 50 + 50))
  return (
    <div className="grid">
      {cols.map((col, i) => (
        <table key={i} className="grid-table">
          <thead>
            <tr><th>Q</th><th>Marked</th><th>Conf</th><th>Edit</th></tr>
          </thead>
          <tbody>
            {col.map((a) => (
              <Row key={a.question} a={a} selected={selectedQ === a.question}
                   onSelect={onSelect} onChange={onChange} />
            ))}
          </tbody>
        </table>
      ))}
    </div>
  )
}
