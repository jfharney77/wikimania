import { useState, useRef, useCallback, useEffect } from 'react'

const API = import.meta.env.VITE_API_URL ?? ''

function formatTime(seconds) {
  const m = Math.floor(seconds / 60)
  const s = seconds % 60
  return `${m}:${s.toString().padStart(2, '0')}`
}

export default function Upload({ wikiId }) {
  const [file, setFile] = useState(null)
  const [dragging, setDragging] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [events, setEvents] = useState([])
  const [phase, setPhase] = useState('')
  const [progress, setProgress] = useState({ n: 0, total: 0 })
  const [docs, setDocs] = useState([])
  const [paused, setPaused] = useState(false)
  const [pausedJobId, setPausedJobId] = useState(null)
  const [parallelWrites, setParallelWrites] = useState(1)
  const [elapsed, setElapsed] = useState(0)
  const inputRef = useRef()
  const listRef = useRef()
  const timerRef = useRef(null)

  useEffect(() => { fetchDocs() }, [wikiId])
  useEffect(() => () => clearInterval(timerRef.current), [])

  function startTimer() {
    setElapsed(0)
    clearInterval(timerRef.current)
    timerRef.current = setInterval(() => setElapsed(s => s + 1), 1000)
  }

  function stopTimer() {
    clearInterval(timerRef.current)
    timerRef.current = null
  }

  async function fetchDocs() {
    try {
      const r = await fetch(`${API}/api/wikis/${wikiId}/documents`)
      const d = await r.json()
      setDocs(d.documents)
    } catch { /* ignore */ }
  }

  function pickFile(f) {
    if (f && f.name.endsWith('.md')) setFile(f)
    else alert('Please select a .md file.')
  }

  const onDrop = useCallback(e => {
    e.preventDefault()
    setDragging(false)
    const f = e.dataTransfer.files[0]
    if (f) pickFile(f)
  }, [])

  function addEvent(ev) {
    setEvents(prev => [...prev, ev])
    setTimeout(() => listRef.current?.scrollTo(0, listRef.current.scrollHeight), 50)
  }

  function attachStream(job_id) {
    const es = new EventSource(`${API}/api/jobs/${job_id}/stream`)

    es.onmessage = e => {
      const ev = JSON.parse(e.data)
      if (ev.type === 'heartbeat') return

      if (ev.type === 'phase') {
        setPhase(ev.message)
        addEvent({ cls: '', text: `▶ ${ev.message}` })
      } else if (ev.type === 'concepts') {
        addEvent({ cls: '', text: `Found ${ev.count} concept(s): ${ev.titles.slice(0, 5).join(', ')}${ev.count > 5 ? '...' : ''}` })
        setProgress({ n: 0, total: ev.count })
      } else if (ev.type === 'article') {
        setProgress(p => ({ ...p, n: ev.n }))
        addEvent({ cls: `ev-${ev.status}`, text: `  ${ev.status === 'created' ? '✦' : '↺'} ${ev.title}` })
      } else if (ev.type === 'stub') {
        addEvent({ cls: 'ev-updated', text: `  ○ stub: ${ev.title}` })
      } else if (ev.type === 'graph_done') {
        setPhase('Graph rebuilt.')
        addEvent({ cls: 'ev-done', text: '⬡ Knowledge graph updated.' })
        notify('Wikimania', 'Knowledge graph has been rebuilt.')
      } else if (ev.type === 'warning') {
        addEvent({ cls: '', text: `⚠ ${ev.message}` })
      } else if (ev.type === 'paused') {
        stopTimer()
        setPhase('Rate limit reached.')
        setPaused(true)
        setPausedJobId(job_id)
        addEvent({ cls: 'ev-updated', text: `⏸ ${ev.message}` })
        setUploading(false)
        fetchDocs()
        es.close()
      } else if (ev.type === 'done') {
        stopTimer()
        setPhase('Done.')
        addEvent({ cls: 'ev-done', text: `✓ ${ev.message}` })
        setFile(null)
        setPaused(false)
        setPausedJobId(null)
        setUploading(false)
        fetchDocs()
        es.close()
      } else if (ev.type === 'error') {
        stopTimer()
        setPhase('Error.')
        addEvent({ cls: 'ev-error', text: `✗ ${ev.message}` })
        setUploading(false)
        fetchDocs()
        es.close()
      }
    }

    es.onerror = () => {
      stopTimer()
      es.close()
      setPhase('Processing in background...')
      addEvent({ cls: 'ev-updated', text: '⏳ Stream disconnected — wiki is still being built on the server. Check the Wiki tab in a minute.' })
      setUploading(false)
      fetchDocs()
    }
  }

  async function handleUpload() {
    if (!file || uploading) return
    setUploading(true)
    setEvents([])
    setPhase('Uploading...')
    setProgress({ n: 0, total: 0 })
    setPaused(false)
    setPausedJobId(null)
    startTimer()

    try {
      const form = new FormData()
      form.append('file', file)
      form.append('parallel_writes', parallelWrites)
      const r = await fetch(`${API}/api/wikis/${wikiId}/documents/upload`, { method: 'POST', body: form })
      if (!r.ok) { const e = await r.json(); throw new Error(e.detail ?? 'Upload failed') }
      const { job_id } = await r.json()

      if ('Notification' in window && Notification.permission === 'default') {
        Notification.requestPermission()
      }

      attachStream(job_id)
    } catch (err) {
      stopTimer()
      addEvent({ cls: 'ev-error', text: `✗ ${err.message}` })
      setUploading(false)
    }
  }

  async function handleResume() {
    if (!pausedJobId || uploading) return
    setUploading(true)
    setPaused(false)
    setPhase('Resuming...')
    startTimer()

    try {
      const r = await fetch(`${API}/api/jobs/${pausedJobId}/resume`, { method: 'POST' })
      if (!r.ok) { const e = await r.json(); throw new Error(e.detail ?? 'Resume failed') }
      attachStream(pausedJobId)
    } catch (err) {
      stopTimer()
      addEvent({ cls: 'ev-error', text: `✗ ${err.message}` })
      setUploading(false)
      setPaused(true)
    }
  }

  function handleDismiss() {
    stopTimer()
    setPaused(false)
    setPausedJobId(null)
    setFile(null)
    setUploading(false)
  }

  function notify(title, body) {
    if ('Notification' in window && Notification.permission === 'granted') {
      new Notification(title, { body })
    }
  }

  const pct = progress.total > 0 ? Math.round((progress.n / progress.total) * 100) : 0
  const showTimer = uploading || paused || (elapsed > 0 && events.length > 0)

  return (
    <div className="upload-tab">
      <h2>Add to Wiki</h2>
      <p>Upload a Markdown file. The LLM will extract concepts and write or expand wiki articles automatically.</p>

      <div
        className={`dropzone${dragging ? ' drag-over' : ''}`}
        onDragOver={e => { e.preventDefault(); setDragging(true) }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
        onClick={() => inputRef.current.click()}
      >
        <input ref={inputRef} type="file" accept=".md" onChange={e => pickFile(e.target.files[0])} />
        <div className="dropzone-icon">📄</div>
        {file
          ? <p><strong>{file.name}</strong> ({(file.size / 1024).toFixed(1)} KB)</p>
          : <p>Drop a <strong>.md file</strong> here or click to browse</p>
        }
      </div>

      <div style={{ display: 'flex', alignItems: 'center', gap: '1rem', marginBottom: '1rem' }}>
        <button className="btn" onClick={handleUpload} disabled={!file || uploading || paused}>
          {uploading ? <><span className="spinner" />Processing...</> : 'Upload & Generate Wiki'}
        </button>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', fontSize: '0.85rem', color: 'var(--muted)' }}>
          <label htmlFor="parallel-select">Parallel writes:</label>
          <select
            id="parallel-select"
            value={parallelWrites}
            onChange={e => setParallelWrites(Number(e.target.value))}
            disabled={uploading}
            style={{ background: 'var(--surface2)', border: '1px solid var(--border)', borderRadius: 'var(--radius)', color: 'var(--text)', padding: '0.25rem 0.5rem', fontSize: '0.85rem' }}
          >
            {[1, 2, 3, 4, 5].map(n => <option key={n} value={n}>{n}</option>)}
          </select>
        </div>
      </div>

      {(events.length > 0 || uploading) && (
        <div className="progress-area">
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <div className="phase-label">{phase}</div>
            {showTimer && (
              <div style={{ fontSize: '0.8rem', color: 'var(--muted)', fontVariantNumeric: 'tabular-nums' }}>
                ⏱ {formatTime(elapsed)}
              </div>
            )}
          </div>
          {progress.total > 0 && (
            <div className="progress-bar-track">
              <div className="progress-bar-fill" style={{ width: `${pct}%` }} />
            </div>
          )}
          <ul className="event-list" ref={listRef}>
            {events.map((ev, i) => (
              <li key={i} className={ev.cls}>{ev.text}</li>
            ))}
          </ul>

          {paused && (
            <div style={{ display: 'flex', gap: '0.75rem', marginTop: '1rem' }}>
              <button className="btn" onClick={handleResume}>Resume</button>
              <button className="btn btn-outline" onClick={handleDismiss}>Dismiss</button>
            </div>
          )}
        </div>
      )}

      {docs.length > 0 && (
        <div className="doc-list">
          <h3>Uploaded Documents</h3>
          {docs.map(d => (
            <div key={d.id} className="doc-item">
              <span>{d.filename}</span>
              <span className={`doc-status status-${d.status}`}>{d.status}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
