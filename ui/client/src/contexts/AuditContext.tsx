/**
 * AuditContext — Persist audit state across route navigation.
 *
 * Problem: AuditRunner uses local useState, so navigating away unmounts the
 * component and destroys all state (auditId, proofText, logs, etc.).
 *
 * Solution: Lift the critical audit state to App-level context. When the user
 * navigates back, AuditRunner re-mounts, reads auditId from context, and
 * useAuditStream reconnects — the backend replays all buffered output.
 */
import { createContext, useContext, useState, type ReactNode } from 'react';

interface AuditContextType {
  // Input state (preserved across navigation)
  proofText: string;
  setProofText: (text: string) => void;
  proofName: string;
  setProofName: (name: string) => void;
  mode: string;
  setMode: (mode: string) => void;

  // Audit run state
  auditId: string | null;
  setAuditId: (id: string | null) => void;

  // Timer state
  startTime: number | null;
  setStartTime: (t: number | null) => void;
}

const AuditContext = createContext<AuditContextType | null>(null);

export function AuditProvider({ children }: { children: ReactNode }) {
  const [proofText, setProofText] = useState('');
  const [proofName, setProofName] = useState('');
  const [mode, setMode] = useState('auto');
  const [auditId, setAuditId] = useState<string | null>(null);
  const [startTime, setStartTime] = useState<number | null>(null);

  return (
    <AuditContext.Provider value={{
      proofText, setProofText,
      proofName, setProofName,
      mode, setMode,
      auditId, setAuditId,
      startTime, setStartTime,
    }}>
      {children}
    </AuditContext.Provider>
  );
}

export function useAuditContext() {
  const ctx = useContext(AuditContext);
  if (!ctx) throw new Error('useAuditContext must be used within AuditProvider');
  return ctx;
}
