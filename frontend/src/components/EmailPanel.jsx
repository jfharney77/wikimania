import { useState, useEffect, useCallback } from 'react'
import { apiFetch } from '../api.js'

const SECTIONS = ['Inbox', 'Search', 'Status', 'Hygiene']

export default function EmailPanel({ wikiId, onOpenArticle }) {
  const [status, setStatus] = useState(null)
  const [section, setSection] = useState('Inbox')
  const [message, setMessage] = useState('')

  const fetchStatus = useCallback(async () => {
    try {
      const r = await apiFetch('GET', `/api/wikis/${wikiId}/gmail/status`)
      if (r.ok) setStatus(await r.json())
    } catch { /* ignore */ }
  }, [wikiId])

  useEffect(() => { setStatus(null); setMessage(''); fetchStatus() }, [fetchStatus])

  async function handleConnect() {
    const r = await apiFetch('GET', `/api/wikis/${wikiId}/gmail/auth-url`)
    if (!r.ok) {
      const e = await r.json()
      setMessage(e.detail ?? 'Could not start Gmail connection.')
      return
    }
    const { url } = await r.json()
    window.open(url, 'wikimania-gmail-oauth', 'width=520,height=640')
    // Poll until the OAuth callback lands.
    const poll = setInterval(async () => {
      const s = await apiFetch('GET', `/api/wikis/${wikiId}/gmail/status`)
      if (s.ok) {
        const d = await s.json()
        if (d.connected) {
          clearInterval(poll)
          setStatus(d)
          setMessage(`Connected as ${d.email}.`)
        }
      }
    }, 2000)
    setTimeout(() => clearInterval(poll), 120000)
  }

  async function handleDisconnect() {
    if (!window.confirm('Disconnect Gmail? Already-imported pages are kept.')) return
    await apiFetch('DELETE', `/api/wikis/${wikiId}/gmail`)
    setMessage('Gmail disconnected.')
    fetchStatus()
  }

  if (!status) return <div className="email-tab"><span className="spinner" /></div>

  if (!status.connected) {
    return (
      <div className="email-tab">
        <h2>Email Ingestion</h2>
        <p>
          Connect a Gmail account (read-only) to import threads into this wiki.
          Label an email <strong>{status.label_name ?? 'wiki'}</strong> or forward it to
          your <strong>+wiki</strong> alias and it becomes a wiki page automatically.
        </p>
        {!status.configured && (
          <p style={{ color: 'var(--yellow)' }}>
            The server is missing GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET — ask an admin to configure Gmail OAuth.
          </p>
        )}
        <button className="btn" onClick={handleConnect} disabled={!status.configured}>
          Connect Gmail
        </button>
        {message && <p style={{ marginTop: '1rem', color: 'var(--muted)' }}>{message}</p>}
      </div>
    )
  }

  return (
    <div className="email-tab">
      <div className="email-header">
        <div>
          <h2>Email Ingestion</h2>
          <p style={{ margin: 0 }}>
            Connected as <strong>{status.email}</strong> · label <strong>{status.label_name}</strong> ·
            forward alias <strong>{status.ingest_alias}</strong>
            {status.last_error && <span style={{ color: 'var(--red)' }}> · last sync error: {status.last_error}</span>}
          </p>
        </div>
        <button className="btn btn-outline btn-sm" onClick={handleDisconnect}>Disconnect</button>
      </div>

      <SyncControls wikiId={wikiId} status={status} onChanged={fetchStatus} setMessage={setMessage} />

      {message && <p style={{ color: 'var(--muted)', fontSize: '0.85rem' }}>{message}</p>}

      <nav className="email-sections">
        {SECTIONS.map(s => (
          <button
            key={s}
            className={`tab${section === s ? ' active' : ''}`}
            onClick={() => setSection(s)}
          >
            {s}
          </button>
        ))}
      </nav>

      {section === 'Inbox' && <ThreadBrowser wikiId={wikiId} onOpenArticle={onOpenArticle} setMessage={setMessage} />}
      {section === 'Search' && <EmailSearch wikiId={wikiId} onOpenArticle={onOpenArticle} />}
      {section === 'Status' && <SyncStatus wikiId={wikiId} onOpenArticle={onOpenArticle} />}
      {section === 'Hygiene' && <Hygiene wikiId={wikiId} setMessage={setMessage} />}
    </div>
  )
}

function SyncControls({ wikiId, status, onChanged, setMessage }) {
  const [label, setLabel] = useState(status.label_name)
  const [syncing, setSyncing] = useState(false)

  async function saveLabel() {
    if (!label.trim() || label === status.label_name) return
    const r = await apiFetch('PATCH', `/api/wikis/${wikiId}/gmail/settings`, { label_name: label.trim() })
    if (r.ok) { setMessage(`Watching label "${label.trim()}".`); onChanged() }
  }

  async function toggleSync() {
    const r = await apiFetch('PATCH', `/api/wikis/${wikiId}/gmail/settings`, { sync_enabled: !status.sync_enabled })
    if (r.ok) onChanged()
  }

  async function syncNow() {
    setSyncing(true)
    setMessage('Syncing...')
    try {
      const r = await apiFetch('POST', `/api/wikis/${wikiId}/gmail/sync`)
      if (!r.ok) { const e = await r.json(); throw new Error(e.detail ?? 'Sync failed') }
      const { stats } = await r.json()
      setMessage(`Sync done — ${stats.ingested} ingested, ${stats.updated} updated, ${stats.skipped} unchanged, ${stats.errors} errors.`)
    } catch (err) {
      setMessage(`Sync failed: ${err.message}`)
    } finally {
      setSyncing(false)
      onChanged()
    }
  }

  return (
    <div className="email-controls">
      <label style={{ fontSize: '0.85rem', color: 'var(--muted)' }}>
        Label:{' '}
        <input
          value={label}
          onChange={e => setLabel(e.target.value)}
          onBlur={saveLabel}
          onKeyDown={e => e.key === 'Enter' && saveLabel()}
          style={{ width: '8rem' }}
        />
      </label>
      <label style={{ fontSize: '0.85rem', color: 'var(--muted)', display: 'flex', alignItems: 'center', gap: '0.35rem' }}>
        <input type="checkbox" checked={status.sync_enabled} onChange={toggleSync} />
        Auto-sync every {status.sync_interval}s
      </label>
      <button className="btn btn-outline btn-sm" onClick={syncNow} disabled={syncing}>
        {syncing ? <><span className="spinner" /> Syncing...</> : 'Sync now'}
      </button>
    </div>
  )
}

function ThreadBrowser({ wikiId, onOpenArticle, setMessage }) {
  const [q, setQ] = useState('')
  const [threads, setThreads] = useState([])
  const [loading, setLoading] = useState(false)
  const [importing, setImporting] = useState(null)

  const load = useCallback(async (query = '') => {
    setLoading(true)
    try {
      const r = await apiFetch('GET', `/api/wikis/${wikiId}/gmail/threads?q=${encodeURIComponent(query)}`)
      if (r.ok) {
        const d = await r.json()
        setThreads(d.threads)
      } else {
        const e = await r.json()
        setMessage(e.detail ?? 'Could not load threads.')
      }
    } finally {
      setLoading(false)
    }
  }, [wikiId, setMessage])

  useEffect(() => { load() }, [load])

  async function handleImport(tid) {
    setImporting(tid)
    try {
      const r = await apiFetch('POST', `/api/wikis/${wikiId}/gmail/threads/${tid}/import`)
      if (!r.ok) { const e = await r.json(); throw new Error(e.detail ?? 'Import failed') }
      const d = await r.json()
      if (d.action === 'skipped') setMessage(`"${d.title}" was already imported — nothing new.`)
      else if (d.action === 'denied') setMessage(`Sender is denylisted — thread not imported.`)
      else setMessage(`"${d.title}" ${d.action} as a wiki page${d.job_id ? ' — topic extraction running in background.' : '.'}`)
      setThreads(prev => prev.map(t => t.thread_id === tid ? { ...t, imported: true, article_id: d.article_id ?? t.article_id } : t))
    } catch (err) {
      setMessage(`Import failed: ${err.message}`)
    } finally {
      setImporting(null)
    }
  }

  return (
    <div>
      <form className="query-form" onSubmit={e => { e.preventDefault(); load(q) }}>
        <input placeholder="Search Gmail (e.g. from:alice subject:report)..." value={q} onChange={e => setQ(e.target.value)} />
        <button className="btn btn-sm" type="submit" disabled={loading}>Search</button>
      </form>
      {loading && <p style={{ color: 'var(--muted)' }}><span className="spinner" /> Loading threads...</p>}
      {!loading && threads.length === 0 && <p style={{ color: 'var(--muted)', fontSize: '0.9rem' }}>No threads found.</p>}
      {threads.map(t => (
        <div key={t.thread_id} className="doc-item">
          <div style={{ minWidth: 0, flex: 1 }}>
            <div style={{ fontWeight: 600, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
              {t.subject} {t.message_count > 1 && <span style={{ color: 'var(--muted)', fontWeight: 400 }}>({t.message_count})</span>}
            </div>
            <div style={{ fontSize: '0.78rem', color: 'var(--muted)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
              {t.from} — {t.snippet}
            </div>
          </div>
          {t.imported ? (
            <button className="btn btn-outline btn-sm" onClick={() => t.article_id && onOpenArticle(t.article_id)}>
              Open page
            </button>
          ) : (
            <button className="btn btn-sm" onClick={() => handleImport(t.thread_id)} disabled={importing === t.thread_id}>
              {importing === t.thread_id ? <><span className="spinner" /> Importing</> : 'Import to wiki'}
            </button>
          )}
        </div>
      ))}
    </div>
  )
}

function EmailSearch({ wikiId, onOpenArticle }) {
  const [filters, setFilters] = useState({ q: '', sender: '', after: '', before: '', topic: '', has_attachment: '' })
  const [results, setResults] = useState(null)
  const [loading, setLoading] = useState(false)

  async function search(e) {
    e?.preventDefault()
    setLoading(true)
    try {
      const params = new URLSearchParams()
      Object.entries(filters).forEach(([k, v]) => { if (v !== '') params.set(k, v) })
      const r = await apiFetch('GET', `/api/wikis/${wikiId}/email/search?${params}`)
      if (r.ok) setResults((await r.json()).results)
    } finally {
      setLoading(false)
    }
  }

  const set = (k) => (e) => setFilters(f => ({ ...f, [k]: e.target.value }))

  return (
    <div>
      <form onSubmit={search} className="email-search-form">
        <input placeholder="Text..." value={filters.q} onChange={set('q')} />
        <input placeholder="Sender..." value={filters.sender} onChange={set('sender')} />
        <input placeholder="Topic..." value={filters.topic} onChange={set('topic')} />
        <input type="date" value={filters.after} onChange={set('after')} title="After" />
        <input type="date" value={filters.before} onChange={set('before')} title="Before" />
        <select value={filters.has_attachment} onChange={set('has_attachment')}>
          <option value="">Attachments: any</option>
          <option value="true">Has attachments</option>
          <option value="false">No attachments</option>
        </select>
        <button className="btn btn-sm" type="submit" disabled={loading}>Search</button>
      </form>
      {results && results.length === 0 && <p style={{ color: 'var(--muted)', fontSize: '0.9rem' }}>No email pages match.</p>}
      {results?.map(r => (
        <div key={r.id} className="doc-item">
          <div style={{ minWidth: 0, flex: 1 }}>
            <div style={{ fontWeight: 600 }}>
              {r.article_title ?? r.subject}
              {r.is_newsletter && <span className="doc-status status-done" style={{ marginLeft: '0.5rem' }}>newsletter</span>}
              {r.has_attachments && <span title="Has attachments"> 📎</span>}
            </div>
            <div style={{ fontSize: '0.78rem', color: 'var(--muted)' }}>
              {r.sender} · {r.last_message_at ? new Date(r.last_message_at).toLocaleDateString() : ''}
            </div>
          </div>
          {r.gmail_link && <a href={r.gmail_link} target="_blank" rel="noreferrer" className="btn btn-outline btn-sm" style={{ textDecoration: 'none' }}>Gmail</a>}
          {r.article_id && <button className="btn btn-outline btn-sm" onClick={() => onOpenArticle(r.article_id)}>Open page</button>}
        </div>
      ))}
    </div>
  )
}

function SyncStatus({ wikiId, onOpenArticle }) {
  const [log, setLog] = useState([])
  const [threads, setThreads] = useState([])
  const [metrics, setMetrics] = useState(null)

  useEffect(() => {
    (async () => {
      const [r1, r2] = await Promise.all([
        apiFetch('GET', `/api/wikis/${wikiId}/email/status`),
        apiFetch('GET', `/api/wikis/${wikiId}/email/metrics`),
      ])
      if (r1.ok) { const d = await r1.json(); setLog(d.log); setThreads(d.threads) }
      if (r2.ok) setMetrics((await r2.json()).metrics)
    })()
  }, [wikiId])

  return (
    <div>
      {metrics && (
        <p style={{ fontSize: '0.85rem', color: 'var(--muted)' }}>
          {metrics.threads_tracked} thread(s) tracked ·
          {' '}{(metrics.totals.ingested ?? 0)} ingested · {(metrics.totals.updated ?? 0)} updated ·
          {' '}{(metrics.totals.dedupe ?? 0)} dedupe hits · {(metrics.totals.denied ?? 0)} denied ·
          {' '}{(metrics.totals.error ?? 0)} errors
        </p>
      )}
      <h3 style={{ fontSize: '0.9rem', margin: '1rem 0 0.5rem' }}>Ingested threads</h3>
      {threads.length === 0 && <p style={{ color: 'var(--muted)', fontSize: '0.85rem' }}>Nothing ingested yet.</p>}
      {threads.slice(0, 20).map(t => (
        <div key={t.id} className="doc-item">
          <span>{t.subject || '(no subject)'}{t.is_newsletter ? ' 📰' : ''}</span>
          {t.article_id && <button className="btn btn-outline btn-sm" onClick={() => onOpenArticle(t.article_id)}>Open page</button>}
        </div>
      ))}
      <h3 style={{ fontSize: '0.9rem', margin: '1.5rem 0 0.5rem' }}>Recent sync activity</h3>
      <ul className="event-list" style={{ maxHeight: 'none' }}>
        {log.length === 0 && <li>No sync activity yet.</li>}
        {log.map(e => (
          <li key={e.id} className={e.action === 'error' ? 'ev-error' : e.action === 'ingested' ? 'ev-created' : 'ev-updated'}>
            {new Date(e.created_at).toLocaleString()} — {e.action}{e.detail ? `: ${e.detail}` : ''}
          </li>
        ))}
      </ul>
    </div>
  )
}

function Hygiene({ wikiId, setMessage }) {
  const [denylist, setDenylist] = useState([])
  const [pattern, setPattern] = useState('')
  const [purgeSenderVal, setPurgeSenderVal] = useState('')
  const [purging, setPurging] = useState(false)

  const load = useCallback(async () => {
    const r = await apiFetch('GET', `/api/wikis/${wikiId}/email/denylist`)
    if (r.ok) setDenylist((await r.json()).denylist)
  }, [wikiId])

  useEffect(() => { load() }, [load])

  async function addPattern(e) {
    e.preventDefault()
    if (!pattern.trim()) return
    await apiFetch('POST', `/api/wikis/${wikiId}/email/denylist`, { pattern: pattern.trim() })
    setPattern('')
    load()
  }

  async function removePattern(id) {
    await apiFetch('DELETE', `/api/wikis/${wikiId}/email/denylist/${id}`)
    load()
  }

  async function purge(e) {
    e.preventDefault()
    const sender = purgeSenderVal.trim()
    if (!sender) return
    if (!window.confirm(`Purge ALL pages, documents and orphaned entities from "${sender}" and add them to the denylist?`)) return
    setPurging(true)
    try {
      const r = await apiFetch('POST', `/api/wikis/${wikiId}/email/purge-sender`, { sender, add_to_denylist: true })
      if (!r.ok) { const err = await r.json(); throw new Error(err.detail ?? 'Purge failed') }
      const d = await r.json()
      setMessage(`Purged ${d.threads_purged} thread(s), ${d.pages_deleted} page(s), ${d.entities_removed} orphaned entit(ies).`)
      setPurgeSenderVal('')
      load()
    } catch (err) {
      setMessage(`Purge failed: ${err.message}`)
    } finally {
      setPurging(false)
    }
  }

  return (
    <div>
      <h3 style={{ fontSize: '0.9rem', marginBottom: '0.5rem' }}>Never-ingest denylist</h3>
      <p style={{ color: 'var(--muted)', fontSize: '0.82rem', marginBottom: '0.75rem' }}>
        Threads from senders matching these patterns (substring match) are skipped during sync and import.
      </p>
      <form className="query-form" onSubmit={addPattern}>
        <input placeholder="sender or domain, e.g. noreply@ or spammer.com" value={pattern} onChange={e => setPattern(e.target.value)} />
        <button className="btn btn-sm" type="submit">Add</button>
      </form>
      {denylist.map(d => (
        <div key={d.id} className="doc-item">
          <span>{d.pattern}</span>
          <button className="btn-danger-outline" title="Remove" onClick={() => removePattern(d.id)}>✕</button>
        </div>
      ))}

      <h3 style={{ fontSize: '0.9rem', margin: '1.5rem 0 0.5rem' }}>Purge a sender</h3>
      <p style={{ color: 'var(--muted)', fontSize: '0.82rem', marginBottom: '0.75rem' }}>
        Removes every ingested thread from this sender, their pages and attachments, and any extracted
        entities left with no other references. Also adds the sender to the denylist.
      </p>
      <form className="query-form" onSubmit={purge}>
        <input placeholder="sender@example.com" value={purgeSenderVal} onChange={e => setPurgeSenderVal(e.target.value)} />
        <button className="btn btn-danger btn-sm" type="submit" disabled={purging}>
          {purging ? <><span className="spinner" /> Purging</> : 'Purge sender'}
        </button>
      </form>
    </div>
  )
}
