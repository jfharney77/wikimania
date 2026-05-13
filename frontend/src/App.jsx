import { useState, useEffect } from 'react'
import Upload from './components/Upload.jsx'
import WikiBrowser from './components/WikiBrowser.jsx'
import Graph from './components/Graph.jsx'
import Query from './components/Query.jsx'
import WikiSelector from './components/WikiSelector.jsx'
import LoginPage from './components/LoginPage.jsx'
import AdminPanel from './components/AdminPanel.jsx'
import { getToken, setToken, apiFetch } from './api.js'

const TABS = ['Upload', 'Wiki', 'Graph', 'Query']

export default function App() {
  const [user, setUser] = useState(null)
  const [authChecked, setAuthChecked] = useState(false)
  const [tab, setTab] = useState('Upload')
  const [wikis, setWikis] = useState([])
  const [currentWiki, setCurrentWiki] = useState(null)
  const [selectedArticleId, setSelectedArticleId] = useState(null)

  // Validate token on mount
  useEffect(() => {
    const token = getToken()
    if (!token) { setAuthChecked(true); return }
    apiFetch('GET', '/api/auth/me').then(async r => {
      if (r.ok) {
        const d = await r.json()
        setUser({ username: d.username, role: d.role })
      } else {
        setToken(null)
      }
      setAuthChecked(true)
    }).catch(() => setAuthChecked(true))
  }, [])

  // Listen for token expiry / 401 from any fetch
  useEffect(() => {
    const handler = () => { setUser(null); setWikis([]); setCurrentWiki(null) }
    window.addEventListener('auth:logout', handler)
    return () => window.removeEventListener('auth:logout', handler)
  }, [])

  useEffect(() => {
    if (user) fetchWikis()
  }, [user])

  async function fetchWikis() {
    try {
      const r = await apiFetch('GET', '/api/wikis')
      if (!r.ok) return
      const d = await r.json()
      setWikis(d.wikis)
      if (d.wikis.length > 0 && !currentWiki) setCurrentWiki(d.wikis[0])
    } catch { /* ignore */ }
  }

  async function handleCreateWiki(name) {
    const r = await apiFetch('POST', '/api/wikis', { name })
    if (!r.ok) return
    const wiki = await r.json()
    setWikis(prev => [wiki, ...prev])
    setCurrentWiki(wiki)
    setSelectedArticleId(null)
  }

  async function handleDeleteWiki(wiki) {
    const r = await apiFetch('DELETE', `/api/wikis/${wiki.id}`)
    if (!r.ok) return
    const remaining = wikis.filter(w => w.id !== wiki.id)
    setWikis(remaining)
    setCurrentWiki(remaining[0] ?? null)
    setSelectedArticleId(null)
  }

  function handleLogout() {
    setToken(null)
    setUser(null)
    setWikis([])
    setCurrentWiki(null)
  }

  function openArticle(id) {
    setSelectedArticleId(id)
    setTab('Wiki')
  }

  if (!authChecked) return null

  if (!user) return <LoginPage onLogin={setUser} />

  const tabs = user.role === 'admin' ? [...TABS, 'Admin'] : TABS

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
          {tabs.map(t => (
            <button
              key={t}
              className={`tab${tab === t ? ' active' : ''}`}
              onClick={() => setTab(t)}
            >
              {t}
            </button>
          ))}
        </nav>
        <div className="topbar-user">
          <span className="topbar-username">{user.username}</span>
          {user.role === 'admin' && <span className="role-badge role-admin">admin</span>}
          <button className="btn btn-outline btn-sm" onClick={handleLogout}>Sign out</button>
        </div>
      </header>

      <main className="content">
        {tab === 'Admin' ? (
          <AdminPanel currentUser={user} />
        ) : !currentWiki ? (
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
