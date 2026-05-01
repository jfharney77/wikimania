import { useEffect, useState, useRef, useCallback } from 'react'
import ForceGraph2D from 'react-force-graph-2d'

const API = import.meta.env.VITE_API_URL ?? ''

// Map community id → color
const COMMUNITY_COLORS = [
  '#6c8cff', '#a78bfa', '#34d399', '#f59e0b',
  '#f87171', '#38bdf8', '#fb7185', '#a3e635',
]

function communityColor(communityId) {
  return COMMUNITY_COLORS[(communityId ?? 0) % COMMUNITY_COLORS.length]
}

export default function Graph({ onNodeClick }) {
  const [graphData, setGraphData] = useState(null)
  const [message, setMessage] = useState('')
  const containerRef = useRef()
  const [dimensions, setDimensions] = useState({ width: 800, height: 600 })

  useEffect(() => {
    fetchGraph()
  }, [])

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
      const r = await fetch(`${API}/api/wiki/graph`)
      const d = await r.json()
      if (!d.graph) { setMessage(d.message ?? 'No graph yet.'); return }

      // graphify exports node_link format: { nodes: [...], links: [...] }
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
    } catch (e) {
      setMessage('Failed to load graph.')
    }
  }

  const nodeCanvasObject = useCallback((node, ctx, globalScale) => {
    const label = node.label
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
      ctx.fillText(label, node.x, node.y + r + fontSize)
    }
  }, [])

  const handleNodeClick = useCallback(node => {
    // node.id is "article_<id>" — extract the numeric id
    const m = String(node.id).match(/article_(\d+)/)
    if (m) onNodeClick(parseInt(m[1], 10))
  }, [onNodeClick])

  // Community legend
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
