import { useParams, useNavigate } from 'react-router-dom';
import { useEffect } from 'react';
import { useReport } from '../hooks/useApi';
import VerdictBadge from '../components/VerdictBadge';
import SorryCard from '../components/SorryCard';
import FidelityMeter from '../components/FidelityMeter';
import type { SorryClassification, CostSummary } from '../types';

function CostTable({ cost }: { cost: CostSummary }) {
  const rounds = Object.entries(cost.per_round);
  return (
    <table className="table">
      <thead>
        <tr>
          <th>Round</th>
          <th>Calls</th>
          <th>Input Tokens</th>
          <th>Output Tokens</th>
          <th>Latency</th>
        </tr>
      </thead>
      <tbody>
        {rounds.map(([name, data]) => (
          <tr key={name}>
            <td style={{ fontFamily: 'var(--font-mono)', color: 'var(--accent)' }}>{name}</td>
            <td>{data.calls}</td>
            <td>{data.input_tokens.toLocaleString()}</td>
            <td>{data.output_tokens.toLocaleString()}</td>
            <td>{(data.latency_ms / 1000).toFixed(1)}s</td>
          </tr>
        ))}
        <tr style={{ fontWeight: 700 }}>
          <td>Total</td>
          <td>{cost.total_calls}</td>
          <td>{cost.total_input_tokens.toLocaleString()}</td>
          <td>{cost.total_output_tokens.toLocaleString()}</td>
          <td>{cost.total_latency_s.toFixed(1)}s</td>
        </tr>
      </tbody>
    </table>
  );
}

function RootCauseTree({ classifications, graph }: {
  classifications: SorryClassification[];
  graph: any;
}) {
  if (!graph?.root_node_ids?.length) return null;

  const typeMap = new Map(classifications.map(c => [c.sorry_id, c]));
  const childrenMap = new Map<string, string[]>();
  for (const edge of graph.edges || []) {
    const children = childrenMap.get(edge.from) || [];
    children.push(edge.to);
    childrenMap.set(edge.from, children);
  }

  const EMOJI: Record<string, string> = {
    A: '🔴', B: '🟢', C: '🟠', D: '🔵', E: '⚪',
  };

  function renderNode(nodeId: string, depth: number): JSX.Element {
    const cls = typeMap.get(nodeId);
    const children = childrenMap.get(nodeId) || [];
    const nodeData = graph.nodes?.find((n: any) => n.sorry_id === nodeId);
    const emoji = cls ? (EMOJI[cls.type] || '❓') : '❓';
    const label = cls ? `[${cls.type}]` : '[?]';
    const impact = nodeData?.impact_score || 0;
    const isCritical = nodeData?.is_on_critical_path;

    return (
      <div key={nodeId} style={{ marginLeft: depth * 24 }}>
        <div style={{
          display: 'flex', alignItems: 'center', gap: 8, padding: '4px 8px',
          borderRadius: 4, marginBottom: 2,
          background: isCritical ? 'var(--sorry-a-bg)' : 'transparent',
        }}>
          <span style={{ fontSize: 14 }}>{emoji}</span>
          <span style={{
            fontFamily: 'var(--font-mono)', fontSize: 12, fontWeight: 600,
            color: cls ? `var(--sorry-${cls.type.toLowerCase()})` : 'var(--text-muted)',
          }}>
            {label}
          </span>
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--text-secondary)' }}>
            {nodeId}
          </span>
          {impact > 0 && (
            <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>
              impact: {impact}
            </span>
          )}
          {isCritical && (
            <span style={{
              fontSize: 10, padding: '1px 6px', borderRadius: 3,
              background: 'var(--sorry-a-bg)', color: 'var(--sorry-a)',
              fontWeight: 600,
            }}>
              CRITICAL PATH
            </span>
          )}
        </div>
        {children.map(childId => renderNode(childId, depth + 1))}
      </div>
    );
  }

  return (
    <div style={{ fontFamily: 'var(--font-mono)', fontSize: 13 }}>
      {graph.root_node_ids.map((rootId: string) => renderNode(rootId, 0))}
    </div>
  );
}

export default function ReportViewer() {
  const { name } = useParams<{ name: string }>();
  const { data: report, isLoading, isError } = useReport(name);
  const navigate = useNavigate();

  // P2 #12: Dynamic page title
  useEffect(() => {
    document.title = report ? `${report.proof_title} — Proof Auditor` : 'Report — Proof Auditor';
  }, [report]);

  if (isLoading) {
    return (
      <div className="empty-state">
        <div className="empty-state-icon" style={{ animation: 'pulse-glow 1.5s infinite' }}>⏳</div>
        <div className="empty-state-title">Loading report...</div>
      </div>
    );
  }

  if (isError || !report) {
    return (
      <div className="empty-state">
        <div className="empty-state-icon">😕</div>
        <div className="empty-state-title">Report not found</div>
        <div className="empty-state-desc">Could not load report: {name}</div>
      </div>
    );
  }

  const verifiedErrors = report.classifications.filter(c => c.type === 'A');
  const verifiedCorrect = report.classifications.filter(c => c.type === 'B');
  const needsReview = report.classifications.filter(c => c.type === 'C' || c.type === 'D' || c.type === 'E');
  const allClassifications = report.classifications;

  return (
    <div style={{ maxWidth: 960, margin: '0 auto' }}>
      {/* Back button */}
      <button
        className="btn btn-secondary"
        onClick={() => navigate(-1)}
        style={{ marginBottom: 20 }}
      >
        ← Back
      </button>

      {/* Header */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: 16,
        marginBottom: 24, flexWrap: 'wrap',
      }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, letterSpacing: '-0.03em' }}>
          {report.proof_title}
        </h1>
        <VerdictBadge verdict={report.final_verdict || report.verdict} />
      </div>

      {/* Adjudicator Narrative — THE MAIN EVENT */}
      {report.adjudication?.narrative && (
        report.adjudication.narrative.diagnosis ||
        report.adjudication.narrative.fix_suggestion ||
        report.adjudication.narrative.impact_assessment
      ) && (
        <div className="card" style={{
          marginBottom: 24,
          border: '1px solid var(--accent)',
          background: 'linear-gradient(135deg, var(--bg-card), color-mix(in srgb, var(--accent) 5%, var(--bg-card)))',
        }}>
          <div className="card-header" style={{ borderBottom: '1px solid var(--accent)' }}>
            <span className="card-title" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <span style={{ fontSize: 18 }}>⚖️</span>
              Final Report
            </span>
            <span style={{
              fontSize: 11, padding: '2px 10px', borderRadius: 12,
              background: 'var(--accent)', color: 'var(--bg-primary)',
              fontWeight: 600, letterSpacing: '0.03em',
            }}>
              ADJUDICATOR • {Math.round((report.adjudication.confidence || 0) * 100)}% confidence
            </span>
          </div>
          <div style={{ padding: '20px 24px', lineHeight: 1.8, fontSize: 14 }}>
            {report.adjudication.narrative.diagnosis && (
              <div style={{ marginBottom: 16 }}>
                <div style={{
                  fontSize: 12, fontWeight: 700, textTransform: 'uppercase',
                  letterSpacing: '0.05em', color: 'var(--sorry-a)',
                  marginBottom: 6,
                }}>
                  📋 Diagnosis
                </div>
                <div style={{ color: 'var(--text-primary)' }}>
                  {report.adjudication.narrative.diagnosis}
                </div>
              </div>
            )}
            {report.adjudication.narrative.fix_suggestion && (
              <div style={{ marginBottom: 16 }}>
                <div style={{
                  fontSize: 12, fontWeight: 700, textTransform: 'uppercase',
                  letterSpacing: '0.05em', color: 'var(--sorry-d)',
                  marginBottom: 6,
                }}>
                  🔧 Fix Suggestion
                </div>
                <div style={{ color: 'var(--text-primary)' }}>
                  {report.adjudication.narrative.fix_suggestion}
                </div>
              </div>
            )}
            {report.adjudication.narrative.impact_assessment && (
              <div>
                <div style={{
                  fontSize: 12, fontWeight: 700, textTransform: 'uppercase',
                  letterSpacing: '0.05em', color: 'var(--sorry-b)',
                  marginBottom: 6,
                }}>
                  💥 Impact Assessment
                </div>
                <div style={{ color: 'var(--text-primary)' }}>
                  {report.adjudication.narrative.impact_assessment}
                </div>
              </div>
            )}
          </div>

          {/* Overrides */}
          {report.adjudication.has_overrides && (
            <div style={{
              padding: '12px 24px',
              borderTop: '1px solid var(--border)',
              background: 'var(--sorry-c-bg)',
            }}>

              <div style={{
                fontSize: 12, fontWeight: 700, color: 'var(--sorry-c)',
                marginBottom: 8,
              }}>
                ⚠️ CLASSIFICATION OVERRIDES
              </div>
              {report.adjudication.overrides
                .filter(o => o.override)
                .map(o => (
                  <div key={o.sorry_id} style={{
                    fontSize: 13, marginBottom: 4,
                    fontFamily: 'var(--font-mono)',
                  }}>
                    🔄 {o.sorry_id}: {o.original_type} → {o.final_type}
                    <span style={{ color: 'var(--text-muted)', marginLeft: 8, fontFamily: 'var(--font-sans)' }}>
                      {o.review_note}
                    </span>
                  </div>
                ))}
            </div>
          )}
        </div>
      )}

      {/* Summary Stats */}
      <div className="grid-4" style={{ marginBottom: 24 }}>
        <div className="stat-card">
          <div className="stat-value">{report.total_sorrys}</div>
          <div className="stat-label">Total Sorry Gaps</div>
        </div>
        <div className="stat-card">
          <div className="stat-value" style={{ color: 'var(--sorry-a)' }}>
            {verifiedErrors.length}
          </div>
          <div className="stat-label">🔴 Refuted</div>
        </div>
        <div className="stat-card">
          <div className="stat-value" style={{ color: 'var(--sorry-b)' }}>
            {verifiedCorrect.length}
          </div>
          <div className="stat-label">🟢 Verified</div>
        </div>
        <div className="stat-card">
          <div className="stat-value" style={{ color: 'var(--sorry-c)' }}>
            {needsReview.length}
          </div>
          <div className="stat-label">⚠️ Needs Review</div>
        </div>
      </div>

      {/* Proof Structure Summary */}
      {report.proof_structure && (
        <div className="card" style={{ marginBottom: 24 }}>
          <div className="card-header">
            <span className="card-title">📐 Proof Structure</span>
            <span style={{
              fontSize: 12, padding: '2px 8px', borderRadius: 8,
              background: 'var(--bg-inset)', color: 'var(--text-muted)',
              fontFamily: 'var(--font-mono)',
            }}>
              {report.proof_structure.proof_strategy}
            </span>
          </div>
          <div style={{ padding: '12px 16px', fontSize: 13, color: 'var(--text-secondary)' }}>
            <div style={{ display: 'flex', gap: 24, flexWrap: 'wrap', marginBottom: 12 }}>
              <span>Steps: <strong>{report.proof_structure.steps.length}</strong></span>
              <span>Sorry: <strong style={{ color: 'var(--sorry-a)' }}>{report.proof_structure.sorry_count}</strong></span>
              <span>Max depth: <strong>{report.proof_structure.max_depth}</strong></span>
              <span>Root: <strong style={{ fontFamily: 'var(--font-mono)' }}>{report.proof_structure.root_steps.join(', ')}</strong></span>
            </div>
            {report.proof_structure.critical_chain.length > 0 && (
              <div style={{
                fontFamily: 'var(--font-mono)', fontSize: 12,
                color: 'var(--sorry-a)', padding: '8px 12px',
                background: 'var(--sorry-a-bg)', borderRadius: 'var(--radius-sm)',
              }}>
                Critical chain: {report.proof_structure.critical_chain.join(' → ')}
              </div>
            )}
          </div>
        </div>
      )}

      {/* Human Review: Side-by-Side Comparison */}
      {report.back_translation && report.back_translation.back_translated_text && (
        <div className="card" style={{ marginBottom: 24 }}>
          <div className="card-header">
            <span className="card-title">🔁 Translation Verification — Human Review</span>
            {report.back_translation.mode === 'web' && (
              <span className="badge" style={{
                background: 'var(--sorry-b-bg)', color: 'var(--sorry-b)',
                border: '1px solid var(--sorry-b)',
              }}>
                Awaiting Your Review
              </span>
            )}
          </div>

          {report.back_translation.mode === 'web' && (
            <div style={{
              padding: '12px 16px', marginBottom: 16,
              background: 'var(--sorry-b-bg)', borderRadius: 'var(--radius-sm)',
              borderLeft: '4px solid var(--sorry-b)',
              fontSize: 13, color: 'var(--text-secondary)', lineHeight: 1.6,
            }}>
              <strong style={{ color: 'var(--sorry-b)' }}>📋 Instructions:</strong> AI 已将 Lean 代码回译为自然语言（右栏）。
              请逐步对比原始证明（左栏）和回译文本，关注：
              <strong>变量一致性</strong>、<strong>量词 ∀/∃</strong>、<strong>逻辑结构</strong>、<strong>假设增减</strong>。
            </div>
          )}

          <div style={{
            display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16,
          }}>
            {/* Left: Original Proof */}
            <div>
              <div style={{
                fontSize: 12, fontWeight: 600, textTransform: 'uppercase',
                letterSpacing: '0.05em', color: 'var(--text-muted)', marginBottom: 8,
              }}>
                📄 Original Proof
              </div>
              <div style={{
                background: 'var(--bg-inset)', border: '1px solid var(--border)',
                borderRadius: 'var(--radius-md)', padding: 16,
                fontFamily: 'var(--font-mono)', fontSize: 12, lineHeight: 1.8,
                color: 'var(--text-primary)', maxHeight: 500, overflowY: 'auto',
                whiteSpace: 'pre-wrap',
              }}>
                {report.back_translation.original_proof}
              </div>
            </div>

            {/* Right: Back-Translated Text */}
            <div>
              <div style={{
                fontSize: 12, fontWeight: 600, textTransform: 'uppercase',
                letterSpacing: '0.05em', color: 'var(--accent)', marginBottom: 8,
              }}>
                🔁 Back-Translation (Lean → Natural Language)
              </div>
              <div style={{
                background: 'var(--bg-inset)', border: '1px solid var(--accent)',
                borderRadius: 'var(--radius-md)', padding: 16,
                fontSize: 13, lineHeight: 1.8,
                color: 'var(--text-primary)', maxHeight: 500, overflowY: 'auto',
                whiteSpace: 'pre-wrap',
              }}>
                {report.back_translation.back_translated_text}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Fidelity (auto mode) */}
      {report.back_translation && report.back_translation.fidelity_score !== null && report.back_translation.fidelity_score >= 0 && (
        <div className="card" style={{ marginBottom: 24 }}>
          <div className="card-header">
            <span className="card-title">Translation Fidelity</span>
            {report.back_translation.translation_attempts && (
              <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>
                {report.back_translation.translation_attempts} attempt(s)
              </span>
            )}
          </div>
          <FidelityMeter score={report.back_translation.fidelity_score} />
          {report.back_translation.flagged_steps.length > 0 && (
            <div style={{ marginTop: 16 }}>
              <div className="section-title">Flagged Steps</div>
              {report.back_translation.flagged_steps.map(step => (
                <div key={step.step_id} style={{
                  padding: '10px 14px', marginBottom: 8,
                  background: 'var(--sorry-b-bg)', borderRadius: 'var(--radius-sm)',
                  borderLeft: '3px solid var(--sorry-b)',
                  fontSize: 13, color: 'var(--text-secondary)',
                }}>
                  <strong style={{ color: 'var(--sorry-b)' }}>Step {step.step_id}</strong>
                  <span style={{ marginLeft: 8, fontSize: 11, color: 'var(--text-muted)' }}>
                    ({Math.round(step.confidence * 100)}% confidence)
                  </span>
                  <div style={{ marginTop: 4 }}>{step.discrepancy}</div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Root Cause Tree */}
      {report.proof_graph && report.proof_graph.edges.length > 0 && (
        <div className="card" style={{ marginBottom: 24 }}>
          <div className="card-header">
            <span className="card-title">Root Cause Tree</span>
            <button
              className="btn btn-secondary"
              style={{ fontSize: 12, padding: '4px 12px' }}
              onClick={() => navigate(`/graph/${name}`)}
            >
              Full Graph →
            </button>
          </div>
          <RootCauseTree classifications={report.classifications} graph={report.proof_graph} />
        </div>
      )}

      {/* Sorry Classifications — grouped by verdict */}
      {verifiedErrors.length > 0 && (
        <div style={{ marginBottom: 24 }}>
          <div className="section-title">🔴 Verified Errors ({verifiedErrors.length})</div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            {verifiedErrors.map(cls => (
              <SorryCard key={cls.sorry_id} classification={cls} />
            ))}
          </div>
        </div>
      )}

      {needsReview.length > 0 && (
        <div style={{ marginBottom: 24 }}>
          <div className="section-title">⚠️ Needs Review ({needsReview.length})</div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            {needsReview.map(cls => (
              <SorryCard key={cls.sorry_id} classification={cls} />
            ))}
          </div>
        </div>
      )}

      {verifiedCorrect.length > 0 && (
        <div style={{ marginBottom: 24 }}>
          <div className="section-title">🟢 Verified Correct ({verifiedCorrect.length})</div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            {verifiedCorrect.map(cls => (
              <SorryCard key={cls.sorry_id} classification={cls} />
            ))}
          </div>
        </div>
      )}

      {/* Cost Summary */}
      {report.cost && (
        <div className="card" style={{ marginTop: 24 }}>
          <div className="card-header">
            <span className="card-title">API Cost Summary</span>
          </div>
          <CostTable cost={report.cost} />
        </div>
      )}
    </div>
  );
}
