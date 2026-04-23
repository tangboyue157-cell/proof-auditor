import { VERDICT_COLORS } from '../types';

const VERDICT_LABELS: Record<string, string> = {
  'VERIFIED_ERROR': 'Verified Error',
  'VERIFIED_CORRECT': 'Verified Correct',
  'NEEDS_REVIEW': 'Needs Review',
  'TRANSLATION_FAILED': 'Translation Failed',
  // Legacy fallbacks
  'ERROR_DETECTED': 'Error Detected',
  'SUSPICIOUS': 'Suspicious',
  'LIKELY_CORRECT': 'Likely Correct',
  'NOT_YET_IMPLEMENTED': 'Not Implemented',
};

const VERDICT_CLASS: Record<string, string> = {
  'VERIFIED_ERROR': 'badge-error',
  'VERIFIED_CORRECT': 'badge-clean',
  'NEEDS_REVIEW': 'badge-suspicious',
  'TRANSLATION_FAILED': 'badge-translation',
  // Legacy fallbacks
  'ERROR_DETECTED': 'badge-error',
  'SUSPICIOUS': 'badge-suspicious',
  'LIKELY_CORRECT': 'badge-clean',
};

export default function VerdictBadge({ verdict }: { verdict: string }) {
  const cls = VERDICT_CLASS[verdict] || '';
  const label = VERDICT_LABELS[verdict] || verdict;

  return (
    <span
      className={`badge ${cls}`}
      style={!cls ? {
        background: 'var(--bg-tertiary)',
        color: 'var(--text-muted)',
        border: '1px solid var(--border)',
      } : undefined}
    >
      {label}
    </span>
  );
}
