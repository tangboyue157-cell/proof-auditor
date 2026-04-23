import type { SorryClassification, SorryType } from '../types';
import { SORRY_TYPE_LABELS, SORRY_TYPE_COLORS, SORRY_TYPE_BG, SORRY_TYPE_EMOJI } from '../types';
import MarkdownBlock from './MarkdownBlock';

export default function SorryCard({ classification }: { classification: SorryClassification }) {
  const c = classification;
  const emoji = SORRY_TYPE_EMOJI[c.type as SorryType] || '❓';
  const label = SORRY_TYPE_LABELS[c.type as SorryType] || c.type;
  const color = SORRY_TYPE_COLORS[c.type as SorryType] || 'var(--text-muted)';
  const bg = SORRY_TYPE_BG[c.type as SorryType] || 'var(--bg-tertiary)';

  return (
    <div className="sorry-card animate-fade-in" data-type={c.type}>
      {/* Header */}
      <div className="sorry-header">
        <span className="sorry-type-badge" style={{
          background: bg,
          color,
          border: `1px solid ${color}`,
        }}>
          {emoji} {c.type}
        </span>
        <span style={{
          fontSize: 12, color: 'var(--text-secondary)',
          fontWeight: 500,
        }}>
          {label}
        </span>
        <span className="sorry-line">L{c.line}</span>
        <span className="sorry-confidence">
          {Math.round(c.confidence * 100)}%
          <span style={{
            display: 'inline-block', width: 40, height: 4,
            background: 'var(--bg-tertiary)', borderRadius: 2,
            marginLeft: 6, verticalAlign: 'middle', overflow: 'hidden',
          }}>
            <span style={{
              display: 'block', height: '100%', borderRadius: 2,
              width: `${c.confidence * 100}%`,
              background: c.confidence >= 0.8 ? color : 'var(--warning)',
            }} />
          </span>
        </span>
        {c.risk_score !== undefined && c.risk_score > 0.3 && (
          <span style={{
            fontSize: 10, padding: '1px 6px', borderRadius: 3,
            background: 'var(--sorry-a-bg)', color: 'var(--sorry-a)',
            fontWeight: 600,
          }}>
            ⚠ HIGH RISK
          </span>
        )}
      </div>

      {/* Verification Score */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: 8,
        padding: '6px 16px', fontSize: 12, color: 'var(--text-muted)',
      }}>
        <span>Score: {(c.verification_score ?? 0.5).toFixed(2)}</span>
        <span style={{
          flex: 1, height: 6, background: 'var(--bg-tertiary)',
          borderRadius: 3, overflow: 'hidden', maxWidth: 200,
        }}>
          <span style={{
            display: 'block', height: '100%', borderRadius: 3,
            width: `${(c.verification_score ?? 0.5) * 100}%`,
            background: (c.verification_score ?? 0.5) === 0 ? 'var(--sorry-a)'
              : (c.verification_score ?? 0.5) === 1 ? 'var(--sorry-b)'
              : (c.verification_score ?? 0.5) < 0.3 ? 'var(--sorry-c)'
              : (c.verification_score ?? 0.5) > 0.7 ? 'var(--sorry-d)'
              : 'var(--sorry-e)',
            transition: 'width 0.3s ease',
          }} />
        </span>
        <span style={{ fontSize: 11 }}>
          {(c.verification_score ?? 0.5) === 0 ? 'Refuted'
            : (c.verification_score ?? 0.5) === 1 ? 'Verified'
            : (c.verification_score ?? 0.5) < 0.3 ? 'Suspect'
            : (c.verification_score ?? 0.5) > 0.7 ? 'Likely Correct'
            : 'Indeterminate'}
        </span>
      </div>

      {/* Goal */}
      {c.goal && c.goal !== 'no goals' && (
        <div className="sorry-goal">
          {c.goal}
        </div>
      )}

      {/* Reasoning */}
      <MarkdownBlock className="sorry-reasoning">
        {c.reasoning}
      </MarkdownBlock>

      {/* Counterexample */}
      {c.counterexample && (
        <div className="sorry-counterexample">
          <strong>🎯 Counterexample:</strong>
          <MarkdownBlock>{c.counterexample}</MarkdownBlock>
        </div>
      )}

      {/* Salvageable */}
      {c.salvageable && (
        <div style={{
          marginTop: 12, padding: '10px 14px',
          background: 'var(--sorry-d-bg)', borderRadius: 'var(--radius-sm)',
          borderLeft: '3px solid var(--sorry-d)',
          fontSize: 13, color: 'var(--text-secondary)',
        }}>
          🔧 <strong style={{ color: 'var(--sorry-d)' }}>Salvageable</strong> — An alternative proof exists
        </div>
      )}

      {/* Blocked by */}
      {c.blocked_by.length > 0 && (
        <div style={{
          marginTop: 8, fontSize: 12, color: 'var(--text-muted)',
          fontFamily: 'var(--font-mono)',
        }}>
          ← blocked by: {c.blocked_by.join(', ')}
        </div>
      )}
    </div>
  );
}
