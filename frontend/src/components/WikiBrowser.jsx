import { useState, useEffect, useCallback, useRef } from 'react'
import ReactMarkdown from 'react-markdown'
import { apiFetch, streamUrl } from '../api.js'

export default function WikiBrowser({ wikiId, selectedId, onSelect }) {
  const [articles, setArticles] = useState([])
  const [query, setQuery] = useState('')
  const [article, setArticle] = useState(null)
  const [loading, setLoading] = useState(false)
  const [confirming, setConfirming] = useState(false)
  const [criticRunning, setCriticRunning] = useState(false)
  const [criticEvents, setCriticEvents] = useState([])
  const [criticPhase, setCriticPhase] = useState('')
  const [showCritic, setShowCritic] = useState(false)
  const criticListRef = useRef()

  useEffect(() => { setArticle(null); setQuery(''); fetchList() }, [wikiId])

  useEffect(() => {
    if (selectedId != null) loadArticle(selectedId)
  }, [selectedId])

  async function fetchList() {
    try {
      const r = await apiFetch('GET', `/api/wikis/${wikiId}/articles`)
      const d = await r.json()
      setArticles(d.articles)
    } catch { /* ignore */ }
  }

  async function loadArticle(id) {
    setLoading(true)
    try {
      const r = await apiFetch('GET', `/api/wikis/${wikiId}/articles/${id}`)
      const d = await r.json()
      setArticle(d)
      onSelect(id)
    } finally {
      setLoading(false)
    }
  }

  async function handleExport() {
    const r = await apiFetch('GET', `/api/wikis/${wikiId}/export`)
    if (!r.ok) return
    const blob = await r.blob()
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    const cd = r.headers.get('Content-Disposition') ?? ''
    a.download = cd.match(/filename=(.+)/)?.[1] ?? 'vault.zip'
    a.click()
    URL.revokeObjectURL(url)
  }

  async function handleReset() {
    const r = await apiFetch('DELETE', `/api/wikis/${wikiId}/content`)
    if (!r.ok) return
    setArticles([])
    setArticle(null)
    setConfirming(false)
    onSelect(null)
  }

  function addCriticEvent(ev) {
    setCriticEvents(prev => [...prev, ev])
    setTimeout(() => criticListRef.current?.scrollTo(0, criticListRef.current.scrollHeight), 50)
  }

  async function handleRunCritic() {
    setCriticRunning(true)
    setCriticEvents([])
    setCriticPhase('Starting critic...')
    setShowCritic(true)
    try {
      const r = await apiFetch('POST', `/api/wikis/${wikiId}/critic`)
      if (!r.ok) { const e = await r.json(); throw new Error(e.detail ?? 'Failed') }
      const { job_id } = await r.json()
      const es = new EventSource(streamUrl(`/api/jobs/${job_id}/stream`))
      es.onmessage = e => {
        const ev = JSON.parse(e.data)
        if (ev.type === 'heartbeat') return
        if (ev.type === 'phase') {
          setCriticPhase(ev.message)
          addCriticEvent({ cls: '', text: `▶ ${ev.message}` })
        } else if (ev.type === 'progress') {
          setCriticPhase(ev.message)
          addCriticEvent({ cls: 'ev-progress', text: `  ${ev.message}` })
        } else if (ev.type === 'duplicate') {
          addCriticEvent({ cls: 'ev-updated', text: `  ↪ Merged "${ev.title}" → "${ev.merged_into}"` })
        } else if (ev.type === 'contradiction') {
          addCriticEvent({ cls: 'ev-updated', text: `  ✎ Fixed contradiction in "${ev.title}": ${ev.issue}` })
        } else if (ev.type === 'done') {
          setCriticPhase('Done.')
          addCriticEvent({ cls: 'ev-done', text: `✓ ${ev.message}` })
          setCriticRunning(false)
          fetchList()
          es.close()
        } else if (ev.type === 'error') {
          setCriticPhase('Error.')
          addCriticEvent({ cls: 'ev-error', text: `✗ ${ev.message}` })
          setCriticRunning(false)
          es.close()
        }
      }
      es.onerror = () => { setCriticRunning(false); es.close() }
    } catch (err) {
      addCriticEvent({ cls: 'ev-error', text: `✗ ${err.message}` })
      setCriticRunning(false)
    }
  }

  const WikilinkRenderer = useCallback(({ children }) => {
    const text = String(children)
    const parts = text.split(/(\[\[[^\]]+\]\])/g)
    return (
      <p>
        {parts.map((part, i) => {
          const m = part.match(/^\[\[([^\]]+)\]\]$/)
          if (m) {
            const title = m[1]
            const target = articles.find(a => a.title === title)
            return (
              <span
                key={i}
                className="wikilink"
                onClick={() => target && loadArticle(target.id)}
                title={target ? `Open: ${title}` : `${title} (not yet in wiki)`}
              >
                {title}
              </span>
            )
          }
          return part
        })}
      </p>
    )
  }, [articles])

  const filtered = articles.filter(a =>
    a.title.toLowerCase().includes(query.toLowerCase())
  )

  return (
    <div className="wiki-tab">
      {confirming && (
        <div className="confirm-overlay">
          <div className="confirm-dialog">
            <h3>Reset wiki content?</h3>
            <p>All {articles.length} articles and the knowledge graph will be deleted. The wiki itself and document history are kept.</p>
            <div className="confirm-actions">
              <button className="btn btn-danger" onClick={handleReset}>Yes, reset content</button>
              <button className="btn btn-outline" onClick={() => setConfirming(false)}>Cancel</button>
            </div>
          </div>
        </div>
      )}

      <div className="wiki-sidebar">
        <div className="wiki-sidebar-header">
          <input
            placeholder="Search articles..."
            value={query}
            onChange={e => setQuery(e.target.value)}
          />
          {articles.length > 0 && (
            <button
              className="btn btn-outline"
              style={{ fontSize: '0.8rem', padding: '0.25rem 0.6rem' }}
              onClick={handleRunCritic}
              disabled={criticRunning}
              title="Run critic agent to find duplicates and contradictions"
            >
              {criticRunning ? <><span className="spinner" /> Critic...</> : '⚡ Critic'}
            </button>
          )}
          {articles.length > 0 && (
            <button
              className="btn-danger-outline"
              title="Reset wiki content"
              onClick={() => setConfirming(true)}
            >
              ✕
            </button>
          )}
        </div>
        <div className="article-list">
          {filtered.length === 0 && (
            <p style={{ padding: '1rem', color: 'var(--muted)', fontSize: '0.85rem' }}>
              {articles.length === 0 ? 'No articles yet. Upload a document.' : 'No matches.'}
            </p>
          )}
          {filtered.map(a => (
            <div
              key={a.id}
              className={`article-item${article?.id === a.id ? ' active' : ''}`}
              onClick={() => loadArticle(a.id)}
            >
              {a.title}
            </div>
          ))}
        </div>
        {showCritic && (
          <div className="critic-panel">
            <div className="critic-panel-header">
              <span className="phase-label">{criticPhase}</span>
              <button
                className="btn-danger-outline"
                onClick={() => { if (!criticRunning) setShowCritic(false) }}
                title="Close"
                disabled={criticRunning}
              >✕</button>
            </div>
            <ul className="critic-event-list" ref={criticListRef}>
              {criticEvents.map((ev, i) => (
                <li key={i} className={ev.cls}>{ev.text}</li>
              ))}
            </ul>
          </div>
        )}
      </div>

      <div className="wiki-reader">
        {loading && <div className="wiki-reader-empty"><span className="spinner" /></div>}

        {!loading && !article && (
          <div className="wiki-reader-empty">
            {articles.length === 0
              ? 'Upload a markdown file to build your wiki.'
              : 'Select an article from the sidebar.'}
          </div>
        )}

        {!loading && article && (
          <div className="wiki-reader-content">
            <h1>{article.title}</h1>
            <div className="wiki-meta">
              Last updated: {new Date(article.updated_at).toLocaleString()}
            </div>
            <div className="markdown">
              <ReactMarkdown components={{ p: WikilinkRenderer }}>
                {article.content}
              </ReactMarkdown>
            </div>
            <div className="wiki-export-btn">
              <button className="btn btn-outline" onClick={handleExport}>
                Export Obsidian Vault (.zip)
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
