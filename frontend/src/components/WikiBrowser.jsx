import { useState, useEffect, useCallback } from 'react'
import ReactMarkdown from 'react-markdown'

const API = import.meta.env.VITE_API_URL ?? ''

export default function WikiBrowser({ selectedId, onSelect }) {
  const [articles, setArticles] = useState([])
  const [query, setQuery] = useState('')
  const [article, setArticle] = useState(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => { fetchList() }, [])

  useEffect(() => {
    if (selectedId != null) loadArticle(selectedId)
  }, [selectedId])

  async function fetchList() {
    try {
      const r = await fetch(`${API}/api/wiki/articles`)
      const d = await r.json()
      setArticles(d.articles)
    } catch { /* ignore */ }
  }

  async function loadArticle(id) {
    setLoading(true)
    try {
      const r = await fetch(`${API}/api/wiki/articles/${id}`)
      const d = await r.json()
      setArticle(d)
      onSelect(id)
    } finally {
      setLoading(false)
    }
  }

  async function handleExport() {
    const r = await fetch(`${API}/api/wiki/export`)
    if (!r.ok) return
    const blob = await r.blob()
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = 'wikimania-vault.zip'
    a.click()
    URL.revokeObjectURL(url)
  }

  // Render [[wikilinks]] as clickable spans
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
      <div className="wiki-sidebar">
        <div className="wiki-sidebar-header">
          <input
            placeholder="Search articles..."
            value={query}
            onChange={e => setQuery(e.target.value)}
          />
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
              <ReactMarkdown
                components={{ p: WikilinkRenderer }}
              >
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
