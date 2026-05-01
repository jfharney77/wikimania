import { useState } from 'react'
import Upload from './components/Upload.jsx'
import WikiBrowser from './components/WikiBrowser.jsx'
import Graph from './components/Graph.jsx'
import Query from './components/Query.jsx'

const TABS = ['Upload', 'Wiki', 'Graph', 'Query']

export default function App() {
  const [tab, setTab] = useState('Upload')
  const [selectedArticleId, setSelectedArticleId] = useState(null)

  function openArticle(id) {
    setSelectedArticleId(id)
    setTab('Wiki')
  }

  return (
    <div className="app">
      <header className="topbar">
        <h1>Wikimania</h1>
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
        {tab === 'Upload' && <Upload />}
        {tab === 'Wiki'   && <WikiBrowser selectedId={selectedArticleId} onSelect={setSelectedArticleId} />}
        {tab === 'Graph'  && <Graph onNodeClick={openArticle} />}
        {tab === 'Query'  && <Query onOpenArticle={openArticle} />}
      </main>
    </div>
  )
}
