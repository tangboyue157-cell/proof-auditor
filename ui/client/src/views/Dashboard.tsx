import { useNavigate } from 'react-router-dom';
import { useEffect } from 'react';
import { useStatus, useReports } from '../hooks/useApi';
import type { ReportListItem } from '../types';
import VerdictBadge from '../components/VerdictBadge';

function StatCard({ value, label, color }: { value: string | number; label: string; color?: string }) {
  return (
    <div className="stat-card">
      <div className="stat-value" style={color ? { color } : undefined}>{value}</div>
      <div className="stat-label">{label}</div>
    </div>
  );
}

function ProviderStatus({ providers }: { providers: Record<string, boolean> }) {
  return (
    <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
      {Object.entries(providers).map(([name, ok]) => (
        <span
          key={name}
          style={{
            fontSize: 12,
            padding: '3px 10px',
            borderRadius: 4,
            background: ok ? 'var(--sorry-d-bg)' : 'var(--bg-tertiary)',
            color: ok ? 'var(--sorry-d)' : 'var(--text-muted)',
            border: `1px solid ${ok ? 'var(--sorry-d)' : 'var(--border)'}`,
            fontWeight: 600,
          }}
        >
          {ok ? '✓' : '✗'} {name}
        </span>
      ))}
    </div>
  );
}

function ReportRow({ report, onClick }: { report: ReportListItem; onClick: () => void }) {
  const date = new Date(report.modified);
  const timeStr = date.toLocaleDateString() + ' ' + date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

  return (
    <div className="report-item animate-fade-in" onClick={onClick}>
      <VerdictBadge verdict={report.verdict} />
      <span className="report-name">{report.proof_title}</span>
      <span className="report-meta" style={{ color: 'var(--text-secondary)' }}>
        {report.total_sorrys} sorry{report.total_sorrys !== 1 ? 's' : ''}
      </span>
      {report.fidelity_score !== null && (
        <span className="report-meta">
          {Math.round(report.fidelity_score * 100)}% fidelity
        </span>
      )}
      <span className="report-meta">{timeStr}</span>
    </div>
  );
}

export default function Dashboard() {
  const { data: status } = useStatus();
  const { data: reports } = useReports();
  const navigate = useNavigate();

  // P2 #12: Dynamic page title
  useEffect(() => {
    document.title = 'History & Reports — Proof Auditor';
  }, []);

  // Compute stats from reports
  const typeACounts = reports?.reduce((sum, r) => {
    // Count reports with verified errors (new verdict) or legacy ERROR_DETECTED
    return sum + (r.verdict === 'VERIFIED_ERROR' || r.verdict === 'ERROR_DETECTED' ? 1 : 0);
  }, 0) ?? 0;

  const verdictDist = reports?.reduce<Record<string, number>>((acc, r) => {
    acc[r.verdict] = (acc[r.verdict] || 0) + 1;
    return acc;
  }, {}) ?? {};

  return (
    <div style={{ maxWidth: 960, margin: '0 auto' }}>
      {/* Hero section */}
      <div style={{ marginBottom: 32 }}>
        <h1 style={{
          fontSize: 22,
          fontWeight: 700,
          letterSpacing: '-0.03em',
          marginBottom: 24,
        }}>
          History & Reports
        </h1>
      </div>

      {/* Stats */}
      <div className="grid-4" style={{ marginBottom: 24 }}>
        <StatCard value={status?.report_count ?? 0} label="Audit Reports" color="var(--accent)" />
        <StatCard value={typeACounts} label="Errors Found" color="var(--sorry-a)" />
        <StatCard value={status?.proof_files?.length ?? 0} label="Benchmark Proofs" />
        <StatCard
          value={status?.version ?? '—'}
          label="Version"
        />
      </div>

      {/* API Providers */}
      {status && (
        <div className="card" style={{ marginBottom: 24 }}>
          <div className="card-header">
            <span className="card-title">AI Providers</span>
          </div>
          <ProviderStatus providers={status.providers} />
        </div>
      )}

      {/* Verdict distribution */}
      {reports && reports.length > 0 && (
        <div className="card" style={{ marginBottom: 24 }}>
          <div className="card-header">
            <span className="card-title">Verdict Distribution</span>
          </div>
          <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap' }}>
            {Object.entries(verdictDist).map(([verdict, count]) => (
              <div key={verdict} style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <VerdictBadge verdict={verdict} />
                <span style={{ fontSize: 14, fontWeight: 700, fontFamily: 'var(--font-mono)' }}>{count}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Report List */}
      <div style={{ marginBottom: 16 }}>
        <div className="section-title">Audit Reports</div>
      </div>
      {reports && reports.length > 0 ? (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          {reports.map(r => (
            <ReportRow key={r.name} report={r} onClick={() => navigate(`/report/${r.name}`)} />
          ))}
        </div>
      ) : (
        <div className="empty-state">
          <div className="empty-state-icon">📋</div>
          <div className="empty-state-title">No audit reports yet</div>
          <div className="empty-state-desc">
            Run your first audit from the Audit tab or use the CLI:
            <code style={{ display: 'block', marginTop: 8, color: 'var(--accent)' }}>
              python scripts/audit.py benchmark/phase0/buggy_proof.txt
            </code>
          </div>
        </div>
      )}
    </div>
  );
}
