import { useState, useEffect } from 'react'
import Upload from './components/Upload.jsx'
import WikiBrowser from './components/WikiBrowser.jsx'
import Graph from './components/Graph.jsx'
import Query from './components/Query.jsx'
import WikiSelector from './components/WikiSelector.jsx'

const API = import.meta.env.VITE_API_URL ?? ''
const TABS = ['Upload', 'Wiki', 'Graph', 'Query']

export default function App() {
  const [tab, setTab] = useState('Upload')
  const [wikis, setWikis] = useState([])
  const [currentWiki, setCurrentWiki] = useState(null)
  const [selectedArticleId, setSelectedArticleId] = useState(null)

  useEffect(() => { fetchWikis() }, [])

  async function fetchWikis() {
    try {
      const r = await fetch(`${API}/api/wikis`)
      const d = await r.json()
      setWikis(d.wikis)
      if (d.wikis.length > 0 && !currentWiki) {
        setCurrentWiki(d.wikis[0])
      }
    } catch { /* ignore */ }
  }

  async function handleCreateWiki(name) {
    const r = await fetch(`${API}/api/wikis`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name }),
    })
    if (!r.ok) return
    const wiki = await r.json()
    setWikis(prev => [wiki, ...prev])
    setCurrentWiki(wiki)
    setSelectedArticleId(null)
  }

  async function handleDeleteWiki(wiki) {
    const r = await fetch(`${API}/api/wikis/${wiki.id}`, { method: 'DELETE' })
    if (!r.ok) return
    const remaining = wikis.filter(w => w.id !== wiki.id)
    setWikis(remaining)
    setCurrentWiki(remaining[0] ?? null)
    setSelectedArticleId(null)
  }

  function openArticle(id) {
    setSelectedArticleId(id)
    setTab('Wiki')
  }

  return (
    <div className="app">
      <header className="topbar">
        <h1>Wikimania</h1>
        <WikiSelector
          wikis={wikis}
          currentWiki={currentWiki}
          onSelect={w => { setCurrentWiki(w); setSelectedArticleId(null) }}
          onCreate={handleCreateWiki}
          onDelete={handleDeleteWiki}
        />
        <nav className="tabs">
          {TABS.map(t => (
            <button
              key={t}
              className={`tab${tab === t ? ' active' : ''}`}
              onClick={() => setTab(t)}
            >
              {t}
            </button>
          ))}
        </nav>
      </header>

      <main className="content">
        {!currentWiki ? (
          <div className="no-wiki">
            <p>Create or select a wiki to get started.</p>
          </div>
        ) : (
          <>
            {tab === 'Upload' && <Upload wikiId={currentWiki.id} />}
            {tab === 'Wiki'   && <WikiBrowser wikiId={currentWiki.id} selectedId={selectedArticleId} onSelect={setSelectedArticleId} />}
            {tab === 'Graph'  && <Graph wikiId={currentWiki.id} onNodeClick={openArticle} />}
            {tab === 'Query'  && <Query wikiId={currentWiki.id} onOpenArticle={openArticle} />}
          </>
        )}
      </main>
    </div>
  )
}
