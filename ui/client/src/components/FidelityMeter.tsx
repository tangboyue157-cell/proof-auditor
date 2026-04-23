interface Props {
  score: number; // 0.0 to 1.0
}

export default function FidelityMeter({ score }: Props) {
  const pct = Math.round(score * 100);
  const color = score >= 0.7 ? 'var(--success)' :
                score >= 0.5 ? 'var(--warning)' : 'var(--danger)';

  return (
    <div className="fidelity-meter">
      <div className="fidelity-bar">
        <div
          className="fidelity-fill"
          style={{
            width: `${pct}%`,
            background: `linear-gradient(90deg, ${color}88, ${color})`,
          }}
        />
      </div>
      <span className="fidelity-value" style={{ color }}>
        {pct}%
      </span>
    </div>
  );
}
