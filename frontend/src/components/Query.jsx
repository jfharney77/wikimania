import { useState } from 'react'
import ReactMarkdown from 'react-markdown'

const API = import.meta.env.VITE_API_URL ?? ''

export default function Query({ wikiId, onOpenArticle }) {
  const [question, setQuestion] = useState('')
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState(null)
  const [error, setError] = useState('')

  async function handleQuery(e) {
    e.preventDefault()
    if (!question.trim() || loading) return
    setLoading(true)
    setResult(null)
    setError('')

    try {
      const r = await fetch(`${API}/api/wikis/${wikiId}/query`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question }),
      })
      if (!r.ok) { const d = await r.json(); throw new Error(d.detail ?? 'Query failed') }
      setResult(await r.json())
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="query-tab">
      <h2>Ask the Wiki</h2>

      <form className="query-form" onSubmit={handleQuery}>
        <input
          placeholder="Ask a question about your wiki..."
          value={question}
          onChange={e => setQuestion(e.target.value)}
          disabled={loading}
        />
        <button className="btn" type="submit" disabled={loading || !question.trim()}>
          {loading ? <><span className="spinner" />Thinking...</> : 'Ask'}
        </button>
      </form>

      {error && <div className="answer-box" style={{ color: 'var(--red)' }}>{error}</div>}

      {result && (
        <div className="answer-box">
          <div className="markdown">
            <ReactMarkdown>{result.answer}</ReactMarkdown>
          </div>
          {result.sources?.length > 0 && (
            <div className="sources">
              <h4>Sources</h4>
              {result.sources.map(s => (
                <span
                  key={s.id}
                  className="source-chip"
                  onClick={() => onOpenArticle(s.id)}
                  title="Open article"
                >
                  {s.title}
                </span>
              ))}
            </div>
          )}
        </div>
      )}

      {!result && !error && !loading && (
        <p style={{ color: 'var(--muted)', fontSize: '0.9rem' }}>
          Questions are answered using only the articles in your wiki.
        </p>
      )}
    </div>
  )
}
