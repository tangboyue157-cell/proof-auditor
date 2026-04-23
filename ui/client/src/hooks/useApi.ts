/**
 * API hooks using React Query
 */
import { useQuery, useMutation } from '@tanstack/react-query';
import type { ReportListItem, AuditReport, ProjectStatus, AuditJobStatus, BenchmarkProof } from '../types';

const API_BASE = '/api';

async function fetchJson<T>(url: string): Promise<T> {
  const res = await fetch(`${API_BASE}${url}`);
  if (!res.ok) throw new Error(`API error ${res.status}: ${await res.text()}`);
  return res.json();
}

/** GET /api/status */
export function useStatus() {
  return useQuery<ProjectStatus>({
    queryKey: ['status'],
    queryFn: () => fetchJson('/status'),
    staleTime: 30_000,
  });
}

/** GET /api/config */
export function useConfig() {
  return useQuery<Record<string, string>>({
    queryKey: ['config'],
    queryFn: () => fetchJson('/config'),
    staleTime: 0, 
  });
}

/** POST /api/config */
export function useSaveConfig() {
  return useMutation<{ success: boolean }, Error, Record<string, string>>({
    mutationFn: async (body) => {
      const res = await fetch(`${API_BASE}/config`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (!res.ok) throw new Error(await res.text());
      return res.json();
    },
  });
}

/** GET /api/reports */
export function useReports() {
  return useQuery<ReportListItem[]>({
    queryKey: ['reports'],
    queryFn: () => fetchJson('/reports'),
    refetchInterval: 10_000,
  });
}

/** GET /api/reports/:name */
export function useReport(name: string | undefined) {
  return useQuery<AuditReport>({
    queryKey: ['report', name],
    queryFn: () => fetchJson(`/reports/${name}`),
    enabled: !!name,
  });
}




/** GET /api/audit/:id */
export function useAuditJob(id: string | null) {
  return useQuery<AuditJobStatus>({
    queryKey: ['audit-job', id],
    queryFn: () => fetchJson(`/audit/${id}`),
    enabled: !!id,
    refetchInterval: (query) => {
      const data = query.state.data as AuditJobStatus | undefined;
      if (data?.status === 'done' || data?.status === 'error') return false;
      return 2000;
    },
  });
}

export function useStartAudit() {
  return useMutation<{ audit_id: string; status: string }, Error, {
    proof_text?: string;
    mode?: string;
    proof_name?: string;
    provider?: string;
    api_base?: string;
    api_key?: string;
    model_name?: string;
    // PDF upload fields
    pdf_base64?: string;
    pdf_filename?: string;
    pdf_backend?: string;
    pdf_pages?: string;
    pdf_theorem?: string;
    // Reference document fields
    ref_pdfs?: {filename: string, base64: string}[];
  }>({
    mutationFn: async (body) => {
      const res = await fetch(`${API_BASE}/audit`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (!res.ok) throw new Error(await res.text());
      return res.json();
    },
  });
}
