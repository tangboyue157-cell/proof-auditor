import { useParams, useNavigate } from 'react-router-dom';
import { useReport } from '../hooks/useApi';
import { useRef, useEffect, useState, useCallback } from 'react';
import type { GraphNode, GraphEdge, SorryClassification, SorryType } from '../types';
import { SORRY_TYPE_COLORS } from '../types';

const TYPE_COLORS_RAW: Record<string, string> = {
  A: '#f85149',  // Refuted — red
  B: '#3fb950',  // Verified — green
  C: '#f0883e',  // Suspect Error — orange
  D: '#58a6ff',  // Likely Correct — blue
  E: '#8b949e',  // Indeterminate — gray
};

interface NodePos { x: number; y: number; }

function layoutGraph(nodes: GraphNode[], edges: GraphEdge[]): Map<string, NodePos> {
  const positions = new Map<string, NodePos>();
  if (nodes.length === 0) return positions;

  // Group by depth
  const depthGroups = new Map<number, GraphNode[]>();
  for (const n of nodes) {
    const group = depthGroups.get(n.depth) || [];
    group.push(n);
    depthGroups.set(n.depth, group);
  }

  const maxDepth = Math.max(...Array.from(depthGroups.keys()));
  const layerSpacing = 120;
  const nodeSpacing = 100;

  for (const [depth, group] of depthGroups) {
    const totalWidth = (group.length - 1) * nodeSpacing;
    const startX = -totalWidth / 2;
    group.forEach((node, i) => {
      positions.set(node.sorry_id, {
        x: startX + i * nodeSpacing,
        y: depth * layerSpacing,
      });
    });
  }

  return positions;
}

export default function ProofGraph() {
  const { name } = useParams<{ name: string }>();
  const { data: report, isLoading } = useReport(name);
  const navigate = useNavigate();
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const [hoveredNode, setHoveredNode] = useState<string | null>(null);
  const [offset, setOffset] = useState({ x: 0, y: 0 });
  const positionsRef = useRef<Map<string, NodePos>>(new Map());

  const graph = report?.proof_graph;
  const classifications = report?.classifications || [];

  // Build type lookup
  const typeMap = new Map(classifications.map(c => [c.sorry_id, c]));

  const draw = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas || !graph) return;

    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const rect = canvas.getBoundingClientRect();
    canvas.width = rect.width * 2;
    canvas.height = rect.height * 2;
    ctx.scale(2, 2);

    const w = rect.width;
    const h = rect.height;
    const centerX = w / 2 + offset.x;
    const centerY = 60 + offset.y;

    // Clear
    ctx.fillStyle = '#f6f8fa';
    ctx.fillRect(0, 0, w, h);

    const positions = layoutGraph(graph.nodes, graph.edges);
    positionsRef.current = positions;

    // Draw edges
    for (const edge of graph.edges) {
      const from = positions.get(edge.from);
      const to = positions.get(edge.to);
      if (!from || !to) continue;

      const fx = centerX + from.x;
      const fy = centerY + from.y;
      const tx = centerX + to.x;
      const ty = centerY + to.y;

      const isCritical = graph.critical_path.includes(edge.from) && graph.critical_path.includes(edge.to);

      ctx.beginPath();
      ctx.moveTo(fx, fy + 18);
      // Bezier curve for visual appeal
      const midY = (fy + 18 + ty - 18) / 2;
      ctx.bezierCurveTo(fx, midY, tx, midY, tx, ty - 18);

      ctx.strokeStyle = isCritical ? '#cf222e' : edge.source === 'ai' ? '#0969da66' : '#8c959f';
      ctx.lineWidth = isCritical ? 2 : 1;
      ctx.stroke();

      // Arrow head
      const angle = Math.atan2(ty - 18 - midY, tx - tx);
      ctx.beginPath();
      ctx.moveTo(tx, ty - 18);
      ctx.lineTo(tx - 5, ty - 24);
      ctx.lineTo(tx + 5, ty - 24);
      ctx.fillStyle = isCritical ? '#cf222e' : '#8c959f';
      ctx.fill();
    }

    // Draw nodes
    for (const node of graph.nodes) {
      const pos = positions.get(node.sorry_id);
      if (!pos) continue;

      const x = centerX + pos.x;
      const y = centerY + pos.y;
      const cls = typeMap.get(node.sorry_id);
      const color = cls ? (TYPE_COLORS_RAW[cls.type] || '#8b949e') : '#8b949e';
      const isHovered = hoveredNode === node.sorry_id;
      const radius = isHovered ? 20 : 16;

      // Glow for critical path
      if (node.is_on_critical_path) {
        ctx.beginPath();
        ctx.arc(x, y, radius + 6, 0, Math.PI * 2);
        const glow = ctx.createRadialGradient(x, y, radius, x, y, radius + 8);
        glow.addColorStop(0, color + '33');
        glow.addColorStop(1, 'transparent');
        ctx.fillStyle = glow;
        ctx.fill();
      }

      // Node circle
      ctx.beginPath();
      ctx.arc(x, y, radius, 0, Math.PI * 2);
      ctx.fillStyle = isHovered ? color : color + '22';
      ctx.fill();
      ctx.strokeStyle = color;
      ctx.lineWidth = 2;
      ctx.stroke();

      // Label
      ctx.font = '600 10px "JetBrains Mono"';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillStyle = isHovered ? '#ffffff' : color;
      const label = cls ? cls.type : '?';
      ctx.fillText(label, x, y);

      // Line number below
      ctx.font = '400 9px "JetBrains Mono"';
      ctx.fillStyle = '#656d76';
      ctx.fillText(`L${node.line}`, x, y + radius + 12);

      // Impact score for root nodes
      if (node.is_root && node.impact_score > 0) {
        ctx.font = '500 9px "Inter"';
        ctx.fillStyle = '#cf222e';
        ctx.fillText(`↓${node.impact_score}`, x, y - radius - 8);
      }
    }

    // Legend
    const legendY = h - 30;
    ctx.font = '500 11px "Inter"';
    const legendItems = [
      { label: 'Static edge', color: '#8c959f' },
      { label: 'AI edge', color: '#0969da' },
      { label: 'Critical path', color: '#cf222e' },
    ];
    let legendX = 16;
    for (const item of legendItems) {
      ctx.beginPath();
      ctx.moveTo(legendX, legendY);
      ctx.lineTo(legendX + 20, legendY);
      ctx.strokeStyle = item.color;
      ctx.lineWidth = 2;
      ctx.stroke();
      ctx.fillStyle = '#656d76';
      ctx.fillText(item.label, legendX + 26, legendY + 4);
      legendX += ctx.measureText(item.label).width + 50;
    }
  }, [graph, hoveredNode, offset, typeMap]);

  useEffect(() => {
    draw();
  }, [draw]);

  // Handle resize
  useEffect(() => {
    const handleResize = () => draw();
    window.addEventListener('resize', handleResize);
    return () => window.removeEventListener('resize', handleResize);
  }, [draw]);

  // Handle mouse hover for node highlighting
  const handleMouseMove = useCallback((e: React.MouseEvent<HTMLCanvasElement>) => {
    const canvas = canvasRef.current;
    if (!canvas || !graph) return;

    const rect = canvas.getBoundingClientRect();
    const mx = e.clientX - rect.left;
    const my = e.clientY - rect.top;
    const centerX = rect.width / 2 + offset.x;
    const centerY = 60 + offset.y;

    let found: string | null = null;
    for (const [nodeId, pos] of positionsRef.current) {
      const x = centerX + pos.x;
      const y = centerY + pos.y;
      const dist = Math.sqrt((mx - x) ** 2 + (my - y) ** 2);
      if (dist < 20) {
        found = nodeId;
        break;
      }
    }
    setHoveredNode(found);
  }, [graph, offset]);

  if (isLoading) {
    return (
      <div className="empty-state">
        <div className="empty-state-icon" style={{ animation: 'pulse-glow 1.5s infinite' }}>⏳</div>
        <div className="empty-state-title">Loading graph...</div>
      </div>
    );
  }

  if (!graph || graph.nodes.length === 0) {
    return (
      <div className="empty-state">
        <div className="empty-state-icon">🕸️</div>
        <div className="empty-state-title">No dependency graph</div>
        <div className="empty-state-desc">This report has no sorry dependency data.</div>
      </div>
    );
  }

  // P2 #12: Dynamic page title
  useEffect(() => {
    document.title = 'Proof Graph — Proof Auditor';
  }, []);

  return (
    <div style={{ maxWidth: 1200, margin: '0 auto' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 16, marginBottom: 20 }}>
        <button className="btn btn-secondary" onClick={() => navigate(-1)}>
          ← Back
        </button>
        <h1 style={{ fontSize: 22, fontWeight: 700, letterSpacing: '-0.03em' }}>
          🕸️ Proof Dependency Graph
        </h1>
      </div>

      {/* Graph info */}
      <div className="grid-4" style={{ marginBottom: 20 }}>
        <div className="stat-card">
          <div className="stat-value">{graph.nodes.length}</div>
          <div className="stat-label">Nodes</div>
        </div>
        <div className="stat-card">
          <div className="stat-value">{graph.edges.length}</div>
          <div className="stat-label">Edges</div>
        </div>
        <div className="stat-card">
          <div className="stat-value">{graph.root_node_ids.length}</div>
          <div className="stat-label">Root Nodes</div>
        </div>
        <div className="stat-card">
          <div className="stat-value">{graph.critical_path.length}</div>
          <div className="stat-label">Critical Path Length</div>
        </div>
      </div>

      {/* Canvas */}
      <div className="graph-container" ref={containerRef}>
        <canvas
          ref={canvasRef}
          style={{ width: '100%', height: '100%', cursor: hoveredNode ? 'pointer' : 'default' }}
          onMouseMove={handleMouseMove}
        />
      </div>

      {/* Hovered node info */}
      {hoveredNode && (
        <div className="card animate-fade-in" style={{ marginTop: 16 }}>
          {(() => {
            const cls = typeMap.get(hoveredNode);
            const node = graph.nodes.find(n => n.sorry_id === hoveredNode);
            if (!cls || !node) return null;
            return (
              <div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 8 }}>
                  <span style={{
                    fontSize: 14, fontWeight: 700, fontFamily: 'var(--font-mono)',
                    color: TYPE_COLORS_RAW[cls.type] || '#8b949e',
                  }}>
                    [{cls.type}] {hoveredNode}
                  </span>
                  <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>
                    Line {cls.line} · Depth {node.depth} · Impact {node.impact_score}
                  </span>
                </div>
                <div style={{ fontSize: 13, color: 'var(--text-secondary)' }}>
                  {cls.reasoning.slice(0, 200)}{cls.reasoning.length > 200 ? '...' : ''}
                </div>
              </div>
            );
          })()}
        </div>
      )}
    </div>
  );
}
