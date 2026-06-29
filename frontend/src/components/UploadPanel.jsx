import { useRef, useState } from 'react'

export default function UploadPanel({ onFile, loading }) {
  const inputRef = useRef(null)
  const [drag, setDrag] = useState(false)

  const pick = (f) => { if (f) onFile(f) }

  return (
    <div
      className={`upload ${drag ? 'drag' : ''}`}
      onDragOver={(e) => { e.preventDefault(); setDrag(true) }}
      onDragLeave={() => setDrag(false)}
      onDrop={(e) => { e.preventDefault(); setDrag(false); pick(e.dataTransfer.files[0]) }}
      onClick={() => inputRef.current?.click()}
    >
      <input
        ref={inputRef}
        type="file"
        accept=".pdf,.png,.jpg,.jpeg,.tif,.tiff,.bmp"
        hidden
        onChange={(e) => pick(e.target.files[0])}
      />
      {loading ? (
        <div className="upload-msg">⏳ Processing sheet…</div>
      ) : (
        <div className="upload-msg">
          <div className="upload-icon">⬆️</div>
          <strong>Drop an OMR sheet here</strong>
          <span>or click to browse — PDF or image</span>
        </div>
      )}
    </div>
  )
}
