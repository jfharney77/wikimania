import { useEffect, useState, useRef, useCallback } from 'react'
import ForceGraph2D from 'react-force-graph-2d'
import { apiFetch } from '../api.js'

const COMMUNITY_COLORS = [
  '#6c8cff', '#a78bfa', '#34d399', '#f59e0b',
  '#f87171', '#38bdf8', '#fb7185', '#a3e635',
]

function communityColor(communityId) {
  return COMMUNITY_COLORS[(communityId ?? 0) % COMMUNITY_COLORS.length]
}

const GRADE_COLOR = { A: '#34d399', B: '#a3e635', C: '#f59e0b', D: '#fb7185', F: '#f87171' }

export default function Graph({ wikiId, onNodeClick }) {
  const [graphData, setGraphData] = useState(null)
  const [evalData, setEvalData] = useState(null)
  const [message, setMessage] = useState('')
  const containerRef = useRef()
  const [dimensions, setDimensions] = useState({ width: 800, height: 600 })

  useEffect(() => { setGraphData(null); setEvalData(null); fetchGraph(); fetchEval() }, [wikiId])

  useEffect(() => {
    if (!containerRef.current) return
    const ro = new ResizeObserver(entries => {
      const { width, height } = entries[0].contentRect
      setDimensions({ width, height })
    })
    ro.observe(containerRef.current)
    return () => ro.disconnect()
  }, [])

  async function fetchGraph() {
    try {
      const r = await apiFetch('GET', `/api/wikis/${wikiId}/graph`)
      const d = await r.json()
      if (!d.graph) { setMessage(d.message ?? 'No graph yet.'); return }

      const raw = d.graph
      const nodes = (raw.nodes ?? []).map(n => ({
        id: n.id,
        label: n.label ?? n.id,
        community: n.community ?? 0,
      }))
      const links = (raw.links ?? []).map(l => ({
        source: l.source,
        target: l.target,
        relation: l.relation ?? '',
      }))
      setGraphData({ nodes, links })
    } catch {
      setMessage('Failed to load graph.')
    }
  }

  async function fetchEval() {
    try {
      const r = await apiFetch('GET', `/api/wikis/${wikiId}/graph/eval`)
      const d = await r.json()
      if (d.eval) setEvalData(d.eval)
    } catch { /* eval is best-effort; ignore failures */ }
  }

  const nodeCanvasObject = useCallback((node, ctx, globalScale) => {
    const fontSize = Math.max(10, 14 / globalScale)
    const r = 6
    ctx.beginPath()
    ctx.arc(node.x, node.y, r, 0, 2 * Math.PI)
    ctx.fillStyle = communityColor(node.community)
    ctx.fill()

    if (globalScale > 1.2) {
      ctx.font = `${fontSize}px Inter, sans-serif`
      ctx.textAlign = 'center'
      ctx.fillStyle = '#e2e8f0'
      ctx.fillText(node.label, node.x, node.y + r + fontSize)
    }
  }, [])

  const handleNodeClick = useCallback(node => {
    const m = String(node.id).match(/article_(\d+)/)
    if (m) onNodeClick(parseInt(m[1], 10))
  }, [onNodeClick])

  const communities = graphData
    ? [...new Set(graphData.nodes.map(n => n.community))].sort((a, b) => a - b)
    : []

  return (
    <div className="graph-tab" ref={containerRef}>
      {!graphData && (
        <div className="graph-empty">
          {message || <><span className="spinner" />Loading graph...</>}
        </div>
      )}

      {graphData && (
        <>
          <ForceGraph2D
            width={dimensions.width}
            height={dimensions.height}
            graphData={graphData}
            nodeCanvasObject={nodeCanvasObject}
            nodeCanvasObjectMode={() => 'replace'}
            linkColor={() => '#2e3350'}
            linkWidth={1}
            backgroundColor="#0f1117"
            onNodeClick={handleNodeClick}
            nodeLabel={node => node.label}
          />
          {evalData && (
            <div className="graph-eval">
              <div className="graph-eval-score">
                <span
                  className="graph-eval-grade"
                  style={{ background: GRADE_COLOR[evalData.grade] ?? '#6c8cff' }}
                >
                  {evalData.grade}
                </span>
                <div>
                  <strong>{evalData.connectivity_score}</strong>/100
                  <div className="graph-eval-sub">connectivity</div>
                </div>
              </div>
              <div className="graph-eval-rows">
                <div><span>Orphans</span><span>{evalData.orphans} ({Math.round(evalData.orphan_rate * 100)}%)</span></div>
                <div><span>Components</span><span>{evalData.components}</span></div>
                <div><span>Largest cluster</span><span>{Math.round(evalData.largest_component_fraction * 100)}%</span></div>
                <div><span>Avg degree</span><span>{evalData.avg_degree}</span></div>
              </div>
            </div>
          )}
          {communities.length > 0 && (
            <div className="graph-legend">
              <h4>Communities</h4>
              {communities.slice(0, 8).map(c => (
                <div key={c} style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 3 }}>
                  <div style={{ width: 10, height: 10, borderRadius: '50%', background: communityColor(c), flexShrink: 0 }} />
                  <span>Group {c}</span>
                </div>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  )
}
