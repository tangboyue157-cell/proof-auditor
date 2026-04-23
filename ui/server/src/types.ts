/**
 * Proof Auditor UI — Shared type definitions (Server)
 */

export interface AuditReport {
  proof_title: string;
  verdict: string;
  total_sorrys: number;
  back_translation?: {
    mode: string;
    fidelity_score: number | null;
    overall_match: boolean | null;
    translation_attempts?: number;
    persistent_low_fidelity?: boolean;
    flagged_steps: Array<{
      step_id: number;
      discrepancy: string;
      confidence: number;
    }>;
  };
  root_causes: string[];
  blocked_descendants: number;
  classifications: SorryClassification[];
  cost?: CostSummary;
  proof_graph?: ProofGraphData;
}

export interface SorryClassification {
  sorry_id: string;
  line: number;
  goal: string;
  type: string; // A | B | C | D | E
  confidence: number;
  verification_score: number; // [0,1] verification score
  reasoning: string;
  blocked_by: string[];
  salvageable: boolean;
  counterexample: string | null;
  risk_score?: number;
}

export interface ProofGraphData {
  nodes: Array<{
    sorry_id: string;
    line: number;
    depth: number;
    impact_score: number;
    is_root: boolean;
    is_leaf: boolean;
    is_on_critical_path: boolean;
    independent_group: number | null;
  }>;
  edges: Array<{
    from: string;
    to: string;
    type: string;
    confidence: number;
    source: string;
    explanation: string;
  }>;
  topo_order: string[];
  critical_path: string[];
  independent_groups: string[][];
  root_node_ids: string[];
}

export interface CostSummary {
  total_calls: number;
  total_input_tokens: number;
  total_output_tokens: number;
  total_latency_s: number;
  per_round: Record<string, {
    calls: number;
    input_tokens: number;
    output_tokens: number;
    latency_ms: number;
  }>;
}

export interface AuditJob {
  id: string;
  status: 'pending' | 'running' | 'done' | 'error';
  proof_title: string;
  started_at: string;
  output: string[];
  result_file?: string;
}
