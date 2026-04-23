/**
 * Proof Auditor UI — Client type definitions v4
 *
 * 5-type classification with [0,1] verification score:
 *   A = Refuted (s=0), B = Verified (s=1),
 *   C = Suspect Error, D = Likely Correct, E = Indeterminate
 */

export interface AuditReport {
  proof_title: string;
  verdict: string;
  diagnostician_verdict?: string;
  final_verdict?: string;
  adjudicator_confidence?: number;
  total_sorrys: number;
  adjudication?: AdjudicationResult;
  back_translation?: {
    mode: string;
    fidelity_score: number | null;
    overall_match: boolean | null;
    translation_attempts?: number;
    persistent_low_fidelity?: boolean;
    back_translated_text?: string | null;
    original_proof?: string | null;
    flagged_steps: FlaggedStep[];
  };
  root_causes: string[];
  blocked_descendants: number;
  classifications: SorryClassification[];
  cost?: CostSummary;
  proof_graph?: ProofGraphData;
  proof_structure?: ProofStructureData;
}

export interface AdjudicationResult {
  final_verdict: string;
  confidence: number;
  has_overrides: boolean;
  overrides: AdjudicationOverride[];
  narrative: {
    diagnosis: string;
    fix_suggestion: string;
    impact_assessment: string;
  };
}

export interface AdjudicationOverride {
  sorry_id: string;
  original_type: string;
  final_type: string;
  override: boolean;
  review_note: string;
}

export interface ProofStructureData {
  theorem_name: string;
  proof_strategy: string;
  steps: { name: string; type: string; line: number; depth: number; has_sorry: boolean; claimed_reason?: string }[];
  edges: { from_step: string; to_step: string; edge_type: string }[];
  sorry_count: number;
  max_depth: number;
  root_steps: string[];
  leaf_steps: string[];
  critical_chain: string[];
}

export interface FlaggedStep {
  step_id: number;
  discrepancy: string;
  confidence: number;
}

export interface SorryClassification {
  sorry_id: string;
  line: number;
  goal: string;
  type: SorryType;
  confidence: number;
  verification_score: number;
  reasoning: string;
  blocked_by: string[];
  salvageable: boolean;
  counterexample: string | null;
  risk_score?: number;
}

export type SorryType = 'A' | 'B' | 'C' | 'D' | 'E';

export type Verdict = 'VERIFIED_ERROR' | 'VERIFIED_CORRECT' | 'NEEDS_REVIEW' | 'TRANSLATION_FAILED';

export interface ProofGraphData {
  nodes: GraphNode[];
  edges: GraphEdge[];
  topo_order: string[];
  critical_path: string[];
  independent_groups: string[][];
  root_node_ids: string[];
}

export interface GraphNode {
  sorry_id: string;
  line: number;
  depth: number;
  impact_score: number;
  is_root: boolean;
  is_leaf: boolean;
  is_on_critical_path: boolean;
  independent_group: number | null;
}

export interface GraphEdge {
  from: string;
  to: string;
  type: string;
  confidence: number;
  source: string;
  explanation: string;
}

export interface CostSummary {
  total_calls: number;
  total_input_tokens: number;
  total_output_tokens: number;
  total_latency_s: number;
  per_round: Record<string, RoundCost>;
}

export interface RoundCost {
  calls: number;
  input_tokens: number;
  output_tokens: number;
  latency_ms: number;
}

export interface ReportListItem {
  name: string;
  file: string;
  proof_title: string;
  verdict: string;
  total_sorrys: number;
  fidelity_score: number | null;
  modified: string;
}

export interface ProjectStatus {
  project: string;
  version: string;
  path: string;
  providers: Record<string, boolean>;
  report_count: number;
  has_benchmark: boolean;
  proof_files: string[];
}

export interface AuditJobStatus {
  id: string;
  status: 'pending' | 'running' | 'done' | 'error';
  proof_title: string;
  started_at: string;
  output_lines: number;
  result_file?: string;
}

export interface BenchmarkProof {
  name: string;
  category: string;
  content: string;
}

// ── Verdict helpers ──

export const VERDICT_COLORS: Record<string, string> = {
  'VERIFIED_ERROR': 'var(--verdict-error)',
  'VERIFIED_CORRECT': 'var(--verdict-clean)',
  'NEEDS_REVIEW': 'var(--verdict-suspicious)',
  'TRANSLATION_FAILED': 'var(--verdict-translation)',
  // Legacy fallbacks
  'ERROR_DETECTED': 'var(--verdict-error)',
  'SUSPICIOUS': 'var(--verdict-suspicious)',
  'LIKELY_CORRECT': 'var(--verdict-clean)',
  'NOT_YET_IMPLEMENTED': 'var(--text-muted)',
};

// ── Sorry type helpers ──

export const SORRY_TYPE_LABELS: Record<SorryType, string> = {
  'A': 'Refuted',
  'B': 'Verified',
  'C': 'Suspect Error',
  'D': 'Likely Correct',
  'E': 'Indeterminate',
};

export const SORRY_TYPE_COLORS: Record<SorryType, string> = {
  'A': 'var(--sorry-a)',
  'B': 'var(--sorry-b)',
  'C': 'var(--sorry-c)',
  'D': 'var(--sorry-d)',
  'E': 'var(--sorry-e)',
};

export const SORRY_TYPE_BG: Record<SorryType, string> = {
  'A': 'var(--sorry-a-bg)',
  'B': 'var(--sorry-b-bg)',
  'C': 'var(--sorry-c-bg)',
  'D': 'var(--sorry-d-bg)',
  'E': 'var(--sorry-e-bg)',
};

export const SORRY_TYPE_EMOJI: Record<SorryType, string> = {
  'A': '🔴',
  'B': '🟢',
  'C': '🟠',
  'D': '🔵',
  'E': '⚪',
};

// ── Verdict grouping helpers ──

export function getVerdictForType(type: SorryType): Verdict {
  switch (type) {
    case 'A': return 'VERIFIED_ERROR';
    case 'B': return 'VERIFIED_CORRECT';
    default: return 'NEEDS_REVIEW';
  }
}

export function getScoreLabel(score: number): string {
  if (score === 0) return 'Mechanically Refuted';
  if (score === 1) return 'Mechanically Verified';
  if (score <= 0.3) return 'Suspect';
  if (score >= 0.7) return 'Likely Correct';
  return 'Indeterminate';
}
