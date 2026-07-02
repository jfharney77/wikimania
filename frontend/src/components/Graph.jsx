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
const PATH_COLOR = '#f59e0b'

const idOf = x => (typeof x === 'object' && x !== null ? x.id : x)
const edgeKey = (a, b) => [a, b].sort().join('|')

export default function Graph({ wikiId, onNodeClick }) {
  const [graphData, setGraphData] = useState(null)
  const [evalData, setEvalData] = useState(null)
  const [message, setMessage] = useState('')
  const [source, setSource] = useState('')
  const [target, setTarget] = useState('')
  const [pathResult, setPathResult] = useState(null)
  const [pathErr, setPathErr] = useState('')
  const containerRef = useRef()
  const [dimensions, setDimensions] = useState({ width: 800, height: 600 })

  useEffect(() => {
    setGraphData(null); setEvalData(null)
    setSource(''); setTarget(''); setPathResult(null); setPathErr('')
    fetchGraph(); fetchEval()
  }, [wikiId])

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

  async function findPath() {
    setPathErr(''); setPathResult(null)
    if (!source || !target) { setPathErr('Pick a source and a target.'); return }
    try {
      const q = `source=${encodeURIComponent(source)}&target=${encodeURIComponent(target)}`
      const r = await apiFetch('GET', `/api/wikis/${wikiId}/graph/path?${q}`)
      const d = await r.json()
      if (!r.ok) { setPathErr(d.detail ?? 'Path lookup failed.'); return }
      if (!d.path) { setPathErr(d.message ?? 'No graph yet.'); return }
      setPathResult(d.path)
      if (!d.path.found) setPathErr('No path — these concepts are not connected.')
    } catch {
      setPathErr('Path lookup failed.')
    }
  }

  function clearPath() {
    setSource(''); setTarget(''); setPathResult(null); setPathErr('')
  }

  const pathIds = pathResult?.found ? new Set(pathResult.nodes.map(n => n.id)) : null
  const pathEdges = pathResult?.found
    ? new Set(pathResult.edges.map(e => edgeKey(e.source, e.target)))
    : null

  const nodeCanvasObject = useCallback((node, ctx, globalScale) => {
    const fontSize = Math.max(10, 14 / globalScale)
    const onPath = pathIds?.has(node.id)
    const dim = pathIds && !onPath
    const r = onPath ? 8 : 6

    ctx.globalAlpha = dim ? 0.25 : 1
    ctx.beginPath()
    ctx.arc(node.x, node.y, r, 0, 2 * Math.PI)
    ctx.fillStyle = onPath ? PATH_COLOR : communityColor(node.community)
    ctx.fill()
    if (onPath) {
      ctx.lineWidth = 2
      ctx.strokeStyle = '#fff'
      ctx.stroke()
    }

    if (onPath || globalScale > 1.2) {
      ctx.font = `${onPath ? 'bold ' : ''}${fontSize}px Inter, sans-serif`
      ctx.textAlign = 'center'
      ctx.fillStyle = onPath ? '#fff' : '#e2e8f0'
      ctx.fillText(node.label, node.x, node.y + r + fontSize)
    }
    ctx.globalAlpha = 1
  }, [pathIds])

  const linkColor = useCallback(link => {
    if (!pathEdges) return '#2e3350'
    return pathEdges.has(edgeKey(idOf(link.source), idOf(link.target))) ? PATH_COLOR : '#181b24'
  }, [pathEdges])

  const linkWidth = useCallback(link => {
    if (!pathEdges) return 1
    return pathEdges.has(edgeKey(idOf(link.source), idOf(link.target))) ? 3 : 1
  }, [pathEdges])

  const handleNodeClick = useCallback(node => {
    const m = String(node.id).match(/article_(\d+)/)
    if (m) onNodeClick(parseInt(m[1], 10))
  }, [onNodeClick])

  const communities = graphData
    ? [...new Set(graphData.nodes.map(n => n.community))].sort((a, b) => a - b)
    : []

  const sortedNodes = graphData
    ? [...graphData.nodes].sort((a, b) => a.label.localeCompare(b.label))
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
            linkColor={linkColor}
            linkWidth={linkWidth}
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

          <div className="graph-path">
            <h4>Shortest path</h4>
            <select value={source} onChange={e => setSource(e.target.value)}>
              <option value="">From…</option>
              {sortedNodes.map(n => <option key={n.id} value={n.id}>{n.label}</option>)}
            </select>
            <select value={target} onChange={e => setTarget(e.target.value)}>
              <option value="">To…</option>
              {sortedNodes.map(n => <option key={n.id} value={n.id}>{n.label}</option>)}
            </select>
            <div className="graph-path-btns">
              <button onClick={findPath}>Find path</button>
              <button className="secondary" onClick={clearPath}>Clear</button>
            </div>
            {pathErr && <div className="graph-path-msg">{pathErr}</div>}
            {pathResult?.found && (
              <div className="graph-path-result">
                <div className="graph-path-hops">{pathResult.length} hop{pathResult.length === 1 ? '' : 's'}</div>
                <div className="graph-path-chain">
                  {pathResult.nodes.map((n, i) => (
                    <span key={n.id}>
                      {i > 0 && <span className="graph-path-arrow"> → </span>}
                      {n.label}
                    </span>
                  ))}
                </div>
              </div>
            )}
          </div>

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
