import { useEffect, useRef, useState, useCallback } from 'react'
import { scanUrl, overlayUrl } from '../api'

// Zoom/pan viewer. Content lives in image-pixel coordinate space and is
// scaled+translated as a whole, so highlight boxes use raw image px.
export default function ImageViewer({ result, view, selectedQ }) {
  const wrapRef = useRef(null)
  const [zoom, setZoom] = useState(0.35)
  const [pan, setPan] = useState({ x: 40, y: 20 })

  const { image_width: W, image_height: H, bubble_half_w: hw, bubble_half_h: hh } = result

  // Fit width on first load / when a new sheet arrives.
  useEffect(() => {
    const el = wrapRef.current
    if (!el) return
    const z = Math.min((el.clientWidth - 40) / W, (el.clientHeight - 40) / H)
    setZoom(z)
    setPan({ x: (el.clientWidth - W * z) / 2, y: 20 })
  }, [result.session_id, W, H])

  // Center the selected question.
  useEffect(() => {
    if (!selectedQ) return
    const a = result.answers.find((x) => x.question === selectedQ)
    if (!a || a.cy == null || !a.centers?.length) return
    const el = wrapRef.current
    const cx = (a.centers[0] + a.centers[a.centers.length - 1]) / 2
    setPan({ x: el.clientWidth / 2 - cx * zoom, y: el.clientHeight / 2 - a.cy * zoom })
  }, [selectedQ]) // eslint-disable-line

  const onWheel = useCallback((e) => {
    e.preventDefault()
    const el = wrapRef.current
    const rect = el.getBoundingClientRect()
    const mx = e.clientX - rect.left
    const my = e.clientY - rect.top
    const factor = e.deltaY < 0 ? 1.15 : 1 / 1.15
    setZoom((z) => {
      const nz = Math.min(3, Math.max(0.1, z * factor))
      setPan((p) => ({ x: mx - (mx - p.x) * (nz / z), y: my - (my - p.y) * (nz / z) }))
      return nz
    })
  }, [])

  const onMouseDown = (e) => {
    const start = { mx: e.clientX, my: e.clientY, px: pan.x, py: pan.y }
    const move = (ev) => setPan({ x: start.px + (ev.clientX - start.mx), y: start.py + (ev.clientY - start.my) })
    const up = () => { window.removeEventListener('mousemove', move); window.removeEventListener('mouseup', up) }
    window.addEventListener('mousemove', move)
    window.addEventListener('mouseup', up)
  }

  const src = view === 'overlay' ? overlayUrl(result.session_id) : scanUrl(result.session_id)
  const sel = selectedQ ? result.answers.find((x) => x.question === selectedQ) : null

  return (
    <div className="viewer" ref={wrapRef} onWheel={onWheel} onMouseDown={onMouseDown}>
      <div
        className="viewer-content"
        style={{ transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})`, width: W, height: H }}
      >
        <img src={src} width={W} height={H} draggable={false} alt="omr sheet" />
        {sel && sel.cy != null && sel.centers?.map((cx, i) => (
          <div
            key={i}
            className={`hl ${result.options[i] === sel.answer ? 'hl-mark' : ''}`}
            style={{ left: cx - hw, top: sel.cy - hh, width: hw * 2, height: hh * 2 }}
          />
        ))}
        {sel && sel.cy != null && (
          <div className="hl-row" style={{
            left: sel.centers[0] - hw - 6, top: sel.cy - hh - 6,
            width: (sel.centers[sel.centers.length - 1] - sel.centers[0]) + hw * 2 + 12,
            height: hh * 2 + 12 }} />
        )}
      </div>
      <div className="viewer-hint">scroll = zoom · drag = pan</div>
    </div>
  )
}
