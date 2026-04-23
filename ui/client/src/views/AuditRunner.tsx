import { useState, useRef, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { useStartAudit } from '../hooks/useApi';
import { useAuditStream } from '../hooks/useAuditStream';
import { useAuditContext } from '../contexts/AuditContext';
import PipelineProgress from '../components/PipelineProgress';

const PIPELINE_ROUNDS = ['R1', 'R1.5', 'R2', 'R2.5', 'R3', 'R4', 'R5'];

function detectRound(line: string): string | null {
  if (line.includes('Round 1:') || line.includes('R1_translation')) return 'R1';
  if (line.includes('Round 1.5') || line.includes('R1.5') || line.includes('Back-Translation')) return 'R1.5';
  if (line.includes('Round 2:') || line.includes('R2_lsp') || line.includes('Lean LSP')) return 'R2';
  if (line.includes('Round 2.5') || line.includes('R2.5') || line.includes('Proof Dependency')) return 'R2.5';
  if (line.includes('Round 3') || line.includes('Rounds 3-5') || line.includes('R3_classification')) return 'R3';
  if (line.includes('Round 4') || line.includes('R4_verification')) return 'R4';
  if (line.includes('Round 5') || line.includes('R5_report') || line.includes('AUDIT REPORT')) return 'R5';
  return null;
}

function classifyLine(line: string): string {
  if (line.includes('✅') || line.includes('SUCCESS') || line.includes('LIKELY_CORRECT')) return 'success';
  if (line.includes('❌') || line.includes('ERROR') || line.includes('Error:')) return 'error';
  if (line.includes('⚠') || line.includes('🔶') || line.includes('WARNING')) return 'warning';
  if (line.includes('🔄') || line.includes('🔍') || line.includes('🧠') || line.includes('🕸') || line.includes('🔁')) return 'round';
  return 'info';
}

export default function AuditRunner() {
  // Use context for state that persists across navigation
  const {
    proofText, setProofText,
    proofName, setProofName,
    mode, setMode,
    auditId, setAuditId,
    startTime, setStartTime,
  } = useAuditContext();

  const { lines, isComplete, finalStatus, resultFile, humanReview, setHumanReview } = useAuditStream(auditId);
  const startAudit = useStartAudit();
  const navigate = useNavigate();
  const logRef = useRef<HTMLDivElement>(null);

  // Local UI state (ok to reset on navigation)
  const [currentRound, setCurrentRound] = useState<string | null>(null);
  const [completedRounds, setCompletedRounds] = useState<Set<string>>(new Set());
  const [elapsed, setElapsed] = useState(0);

  // PDF-specific state
  const [pdfData, setPdfData] = useState<string | null>(null);  // base64 PDF content
  const [pdfFileName, setPdfFileName] = useState<string>('');
  const [pdfFileSize, setPdfFileSize] = useState<number>(0);
  const [pdfBackend, setPdfBackend] = useState<string>('pymupdf');
  const [pdfPages, setPdfPages] = useState<string>('');
  const [pdfTheorem, setPdfTheorem] = useState<string>('');
  const isPdf = pdfData !== null;

  // Reference documents state
  const [refFiles, setRefFiles] = useState<{filename: string, base64: string, size: number}[]>([]);
  const [showRefs, setShowRefs] = useState(false);

  // P2 #12: Dynamic page title
  useEffect(() => {
    document.title = auditId ? 'Running Audit… — Proof Auditor' : 'Audit Runner — Proof Auditor';
  }, [auditId]);

  useEffect(() => {
    // Auto-scroll log
    if (logRef.current) {
      logRef.current.scrollTop = logRef.current.scrollHeight;
    }
    // Detect current round from the latest lines
    for (let i = lines.length - 1; i >= Math.max(0, lines.length - 5); i--) {
      const round = detectRound(lines[i]);
      if (round) {
        setCurrentRound(round);
        // Mark all previous rounds as completed
        const idx = PIPELINE_ROUNDS.indexOf(round);
        if (idx > 0) {
          setCompletedRounds(prev => {
            const next = new Set(prev);
            for (let j = 0; j < idx; j++) next.add(PIPELINE_ROUNDS[j]);
            return next;
          });
        }
        break;
      }
    }
  }, [lines]);

  // Mark all rounds complete when done
  useEffect(() => {
    if (isComplete && finalStatus === 'done') {
      setCompletedRounds(new Set(PIPELINE_ROUNDS));
      setCurrentRound(null);
    }
  }, [isComplete, finalStatus]);

  // Timer tick
  useEffect(() => {
    if (!auditId || isComplete) return;
    // Calculate elapsed from startTime (handles re-mount)
    if (startTime) {
      setElapsed(Math.floor((Date.now() - startTime) / 1000));
    }
    const interval = setInterval(() => {
      if (startTime) {
        setElapsed(Math.floor((Date.now() - startTime) / 1000));
      }
    }, 1000);
    return () => clearInterval(interval);
  }, [auditId, isComplete, startTime]);

  const handleFileUpload = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    
    const nameWithoutExt = file.name.replace(/\.[^/.]+$/, "");
    setProofName(nameWithoutExt);

    if (file.name.toLowerCase().endsWith('.pdf')) {
      // PDF: read as binary and encode to base64
      const reader = new FileReader();
      reader.onload = (event) => {
        const arrayBuffer = event.target?.result as ArrayBuffer;
        const bytes = new Uint8Array(arrayBuffer);
        let binary = '';
        for (let i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i]);
        const base64 = btoa(binary);
        setPdfData(base64);
        setPdfFileName(file.name);
        setPdfFileSize(file.size);
        setProofText('');  // Clear text area — PDF content is binary
      };
      reader.readAsArrayBuffer(file);
    } else {
      // Text/LaTeX: read as text
      setPdfData(null);
      setPdfFileName('');
      setPdfFileSize(0);
      const reader = new FileReader();
      reader.onload = (event) => {
        setProofText(event.target?.result as string);
      };
      reader.readAsText(file);
    }
    e.target.value = '';
  };

  const [isDragging, setIsDragging] = useState(false);

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(true);
  };

  const handleDragLeave = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);
    const file = e.dataTransfer.files?.[0];
    if (!file) return;

    const nameWithoutExt = file.name.replace(/\.[^/.]+$/, "");
    setProofName(nameWithoutExt);

    if (file.name.toLowerCase().endsWith('.pdf')) {
      const reader = new FileReader();
      reader.onload = (event) => {
        const arrayBuffer = event.target?.result as ArrayBuffer;
        const bytes = new Uint8Array(arrayBuffer);
        let binary = '';
        for (let i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i]);
        setPdfData(btoa(binary));
        setPdfFileName(file.name);
        setPdfFileSize(file.size);
        setProofText('');
      };
      reader.readAsArrayBuffer(file);
    } else {
      setPdfData(null);
      setPdfFileName('');
      setPdfFileSize(0);
      const reader = new FileReader();
      reader.onload = (event) => {
        setProofText(event.target?.result as string);
      };
      reader.readAsText(file);
    }
  };

  const handleClearPdf = () => {
    setPdfData(null);
    setPdfFileName('');
    setPdfFileSize(0);
    setPdfPages('');
    setPdfTheorem('');
  };

  const canSubmit = isPdf || proofText.trim().length > 0;

  const handleSubmit = () => {
    if (!canSubmit) return;
    setAuditId(null);
    setCurrentRound(null);
    setCompletedRounds(new Set());
    setStartTime(Date.now());

    const payload: Record<string, any> = {
      mode,
      proof_name: proofName || undefined,
    };

    if (isPdf) {
      payload.pdf_base64 = pdfData;
      payload.pdf_filename = pdfFileName;
      payload.pdf_backend = pdfBackend;
      if (pdfPages.trim()) payload.pdf_pages = pdfPages.trim();
      if (pdfTheorem.trim()) payload.pdf_theorem = pdfTheorem.trim();
    } else {
      payload.proof_text = proofText;
    }

    // Attach reference documents if any
    if (refFiles.length > 0) {
      payload.ref_pdfs = refFiles.map(f => ({ filename: f.filename, base64: f.base64 }));
    }

    startAudit.mutate(payload as any,
      {
        onSuccess: (data) => {
          setAuditId(data.audit_id);
        },
      }
    );
  };

  const handleCancel = async () => {
    if (!auditId) return;
    try {
      await fetch(`/api/audit/${auditId}`, { method: 'DELETE' });
    } catch {
      // Best-effort cancel
    }
  };

  const sendDecision = async (action: 'approve' | 'retry' | 'abort', feedback?: string) => {
    if (!auditId) return;
    try {
      const resp = await fetch(`/api/audit/${auditId}/decision`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action, feedback }),
      });
      if (!resp.ok) {
        const error = await resp.json();
        throw new Error(error.error || 'Failed to submit decision');
      }
      setHumanReview(null); // Clear the panel only on success
    } catch (e: any) {
      alert(`Error: ${e.message}`);
    }
  };

  const isRunning = auditId !== null && !isComplete;

  return (
    <div style={{ maxWidth: 960, margin: '0 auto' }}>
      <h1 style={{
        fontSize: 22,
        fontWeight: 700,
        letterSpacing: '-0.03em',
        marginBottom: 20,
      }}>
        Audit Runner
      </h1>

      {/* Input Form */}
      <div className="card" style={{ marginBottom: 24 }}>
        <div className="card-header">
          <span className="card-title">Proof Input / Upload</span>
          <div style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
            <label style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
              Back-Translation:
              <select
                className="select"
                value={mode}
                onChange={e => setMode(e.target.value)}
                style={{ marginLeft: 8 }}
              >
                <option value="auto">Auto</option>
                <option value="web">Human Review</option>
                <option value="off">Off</option>
              </select>
            </label>
          </div>
        </div>

        <div style={{ display: 'flex', gap: 12, marginBottom: 12 }}>
          <input 
             type="text" className="input" style={{ flex: 1 }} 
             placeholder="Audit run name (Optional)" 
             value={proofName} onChange={e => setProofName(e.target.value)} disabled={isRunning}
          />
          <label className="btn" style={{ cursor: isRunning ? 'not-allowed' : 'pointer', border: '1px solid var(--border)', background: 'var(--bg-tertiary)' }}>
            Upload .tex / .txt / .pdf
            <input 
              type="file" accept=".tex,.txt,.md,.pdf" 
              onChange={handleFileUpload} 
              style={{ display: 'none' }}
              disabled={isRunning}
            />
          </label>
        </div>

        {isPdf ? (
          /* PDF Preview Card */
          <div
            style={{
              minHeight: '180px',
              border: '1px solid var(--border)',
              borderRadius: 'var(--radius-md)',
              background: 'var(--bg-tertiary)',
              padding: 20,
              display: 'flex',
              flexDirection: 'column',
              gap: 16,
            }}
            onDragOver={handleDragOver}
            onDragLeave={handleDragLeave}
            onDrop={handleDrop}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
              <span style={{ fontSize: 32 }}>📄</span>
              <div>
                <div style={{ fontWeight: 600, fontSize: 14 }}>{pdfFileName}</div>
                <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>
                  {(pdfFileSize / 1024).toFixed(0)} KB • PDF file
                </div>
              </div>
              {!isRunning && (
                <button
                  className="btn"
                  onClick={handleClearPdf}
                  style={{
                    marginLeft: 'auto',
                    fontSize: 11, padding: '4px 10px',
                    background: 'var(--sorry-a-bg)', color: 'var(--sorry-a)',
                    border: '1px solid var(--sorry-a)',
                  }}
                >
                  Remove
                </button>
              )}
            </div>

            {/* PDF extraction options */}
            <div style={{
              display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 12,
              padding: '12px 16px',
              background: 'var(--bg-primary)', borderRadius: 'var(--radius-sm)',
              border: '1px solid var(--border)',
            }}>
              <label style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
                Backend
                <select
                  className="select"
                  value={pdfBackend}
                  onChange={e => setPdfBackend(e.target.value)}
                  disabled={isRunning}
                  style={{ display: 'block', marginTop: 4, width: '100%' }}
                >
                  <option value="pymupdf">pymupdf (free, fast)</option>
                  <option value="ai-enhance">ai-enhance (AI fix LaTeX)</option>
                  <option value="vision">vision (scanned PDF)</option>
                </select>
              </label>
              <label style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
                Pages (optional)
                <input
                  type="text" className="input"
                  placeholder="e.g. 3-5,8"
                  value={pdfPages}
                  onChange={e => setPdfPages(e.target.value)}
                  disabled={isRunning}
                  style={{ display: 'block', marginTop: 4, width: '100%' }}
                />
              </label>
              <label style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
                Theorem (optional)
                <input
                  type="text" className="input"
                  placeholder="e.g. 4.1"
                  value={pdfTheorem}
                  onChange={e => setPdfTheorem(e.target.value)}
                  disabled={isRunning}
                  style={{ display: 'block', marginTop: 4, width: '100%' }}
                />
              </label>
            </div>
          </div>
        ) : (
          <textarea
            className="textarea"
            placeholder={isDragging ? 'Drop your file here...' : "Paste your mathematical proof here or drag & drop a file (.tex, .txt, .pdf)...\n\nExample:\nTheorem: The sum of two odd integers is even.\nProof: Let a and b be odd integers..."}
            value={proofText}
            onChange={e => setProofText(e.target.value)}
            onDragOver={handleDragOver}
            onDragLeave={handleDragLeave}
            onDrop={handleDrop}
            disabled={isRunning}
            style={{
              minHeight: '180px',
              transition: 'all 0.2s ease',
              border: isDragging ? '2px dashed var(--accent)' : '1px solid var(--border)',
              background: isDragging ? 'var(--bg-tertiary)' : 'var(--bg-primary)'
            }}
          />
        )}

        {/* Reference Documents Section */}
        <div style={{ marginTop: 12 }}>
          <button
            className="btn"
            onClick={() => setShowRefs(!showRefs)}
            disabled={isRunning}
            style={{
              fontSize: 12, padding: '6px 12px',
              background: refFiles.length > 0 ? 'var(--sorry-d-bg)' : 'var(--bg-tertiary)',
              border: `1px solid ${refFiles.length > 0 ? 'var(--sorry-d)' : 'var(--border)'}`,
              color: refFiles.length > 0 ? 'var(--sorry-d)' : 'var(--text-secondary)',
            }}
          >
            📚 Reference Documents {refFiles.length > 0 ? `(${refFiles.length})` : ''} {showRefs ? '▾' : '▸'}
          </button>

          {showRefs && (
            <div style={{
              marginTop: 8, padding: 16,
              border: '1px solid var(--border)', borderRadius: 'var(--radius-md)',
              background: 'var(--bg-tertiary)',
            }}>
              <div style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 12 }}>
                Upload PDFs that the proof cites. AI will extract theorems and provide them to the Diagnostician & Verifier agents.
              </div>

              {/* Ref file list */}
              {refFiles.map((rf, idx) => (
                <div key={idx} style={{
                  display: 'flex', alignItems: 'center', gap: 8,
                  padding: '6px 10px', marginBottom: 6,
                  background: 'var(--bg-primary)', borderRadius: 'var(--radius-sm)',
                  border: '1px solid var(--border)', fontSize: 13,
                }}>
                  <span>📄</span>
                  <span style={{ flex: 1 }}>{rf.filename}</span>
                  <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>
                    {(rf.size / 1024).toFixed(0)} KB
                  </span>
                  {!isRunning && (
                    <button
                      onClick={() => setRefFiles(prev => prev.filter((_, i) => i !== idx))}
                      style={{
                        background: 'none', border: 'none', cursor: 'pointer',
                        color: 'var(--sorry-a)', fontSize: 14, padding: '0 4px',
                      }}
                    >✕</button>
                  )}
                </div>
              ))}

              {/* Upload button */}
              {refFiles.length < 5 && !isRunning && (
                <label className="btn" style={{
                  cursor: 'pointer', fontSize: 12, padding: '6px 12px',
                  border: '1px dashed var(--border)', background: 'var(--bg-primary)',
                  display: 'inline-block', marginTop: 4,
                }}>
                  + Add Reference PDF
                  <input
                    type="file" accept=".pdf" multiple
                    style={{ display: 'none' }}
                    onChange={(e) => {
                      const files = e.target.files;
                      if (!files) return;
                      const remaining = 5 - refFiles.length;
                      const toAdd = Array.from(files).slice(0, remaining);
                      toAdd.forEach(file => {
                        const reader = new FileReader();
                        reader.onload = (ev) => {
                          const buf = ev.target?.result as ArrayBuffer;
                          const bytes = new Uint8Array(buf);
                          let bin = '';
                          for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
                          setRefFiles(prev => [...prev, {
                            filename: file.name,
                            base64: btoa(bin),
                            size: file.size,
                          }]);
                        };
                        reader.readAsArrayBuffer(file);
                      });
                      e.target.value = '';
                    }}
                  />
                </label>
              )}
              {refFiles.length >= 5 && (
                <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 4 }}>
                  Maximum 5 reference documents reached.
                </div>
              )}
            </div>
          )}
        </div>

        <div style={{ marginTop: 16, display: 'flex', gap: 12, alignItems: 'center' }}>
          <button
            className="btn btn-primary"
            onClick={handleSubmit}
            disabled={isRunning || !canSubmit}
          >
            {isRunning ? 'Running...' : 'Start Audit'}
          </button>
          {startAudit.isError && (
            <span style={{ color: 'var(--danger)', fontSize: 13 }}>
              Error: {startAudit.error.message}
            </span>
          )}
        </div>
      </div>

      {/* Pipeline Progress */}
      {auditId && (
        <div className="card animate-fade-in" style={{ marginBottom: 24 }}>
          <div className="card-header">
            <span className="card-title">Pipeline Progress</span>
            {isComplete && (
              <span className={`badge ${finalStatus === 'done' ? 'badge-clean' : 'badge-error'}`}>
                {finalStatus === 'done' ? '✓ Complete' : '✗ Error'}
              </span>
            )}
            {(auditId && !isComplete || elapsed > 0) && (
              <span style={{ fontSize: 12, color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>
                {Math.floor(elapsed / 60)}m {String(elapsed % 60).padStart(2, '0')}s
              </span>
            )}
            {isRunning && (
              <button
                className="btn"
                onClick={handleCancel}
                style={{
                  fontSize: 11, padding: '3px 10px',
                  background: 'var(--sorry-a-bg)', color: 'var(--sorry-a)',
                  border: '1px solid var(--sorry-a)',
                }}
              >
                Stop
              </button>
            )}
          </div>
          <PipelineProgress
            rounds={PIPELINE_ROUNDS}
            currentRound={currentRound}
            completedRounds={completedRounds}
            isComplete={isComplete}
            hasError={finalStatus === 'error'}
          />
        </div>
      )}

      {/* Log Stream */}
      {lines.length > 0 && (
        <div className="card animate-fade-in" style={{ marginBottom: 24 }}>
          <div className="card-header">
            <span className="card-title">Audit Log</span>
            <span style={{ fontSize: 12, color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>
              {lines.length} lines
            </span>
          </div>
          <div className="log-stream" ref={logRef}>
            {lines.map((line, i) => (
              <div key={i} className={`log-line ${classifyLine(line)}`}>
                {line}
              </div>
            ))}
            {isRunning && (
              <div className="log-line" style={{ opacity: 0.5, animation: 'pulse-glow 1.5s infinite' }}>
                ▍
              </div>
            )}
          </div>
        </div>
      )}

      {/* Human Review Decision Panel */}
      {humanReview && (
        <div className="card animate-fade-in" style={{ marginBottom: 24, border: '2px solid var(--accent)' }}>
          <div className="card-header">
            <span className="card-title">⏸ Human Review — Translation Verification</span>
            <span className="badge" style={{
              background: humanReview.fidelity_score >= 0.7 ? 'var(--sorry-d-bg)' : 'var(--sorry-a-bg)',
              color: humanReview.fidelity_score >= 0.7 ? 'var(--sorry-d)' : 'var(--sorry-a)',
              border: `1px solid ${humanReview.fidelity_score >= 0.7 ? 'var(--sorry-d)' : 'var(--sorry-a)'}`,
            }}>
              AI Reference: {Math.round(humanReview.fidelity_score * 100)}% fidelity
            </span>
          </div>

          <div style={{
            padding: '12px 16px', marginBottom: 16,
            background: 'var(--bg-tertiary)', borderRadius: 'var(--radius-sm)',
            borderLeft: '4px solid var(--accent)',
            fontSize: 13, color: 'var(--text-secondary)', lineHeight: 1.6,
          }}>
            AI 已将 Lean 代码回译为自然语言（右栏）并给出了参考评分。
            请逐步对比原始证明和回译文本，关注<strong>变量一致性</strong>、<strong>量词 ∀/∃</strong>、<strong>逻辑结构</strong>、<strong>假设增减</strong>。
            {humanReview.attempt > 1 && (
              <span style={{ marginLeft: 8, fontWeight: 600, color: 'var(--sorry-b)' }}>
                (第 {humanReview.attempt} 次翻译)
              </span>
            )}
          </div>

          {/* Side-by-side comparison */}
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16, marginBottom: 16 }}>
            <div>
              <div style={{ fontSize: 12, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.05em', color: 'var(--text-muted)', marginBottom: 8 }}>
                📄 Original Proof
              </div>
              <div style={{
                background: 'var(--bg-inset)', border: '1px solid var(--border)',
                borderRadius: 'var(--radius-md)', padding: 16,
                fontFamily: 'var(--font-mono)', fontSize: 12, lineHeight: 1.8,
                color: 'var(--text-primary)', maxHeight: 400, overflowY: 'auto',
                whiteSpace: 'pre-wrap',
              }}>
                {humanReview.original_proof}
              </div>
            </div>
            <div>
              <div style={{ fontSize: 12, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.05em', color: 'var(--accent)', marginBottom: 8 }}>
                🔁 Back-Translation (Lean → Natural Language)
              </div>
              <div style={{
                background: 'var(--bg-inset)', border: '1px solid var(--accent)',
                borderRadius: 'var(--radius-md)', padding: 16,
                fontSize: 13, lineHeight: 1.8,
                color: 'var(--text-primary)', maxHeight: 400, overflowY: 'auto',
                whiteSpace: 'pre-wrap',
              }}>
                {humanReview.back_translated_text}
              </div>
            </div>
          </div>

          {/* Flagged steps */}
          {humanReview.flagged_steps.length > 0 && (
            <div style={{ marginBottom: 16 }}>
              <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--sorry-b)', marginBottom: 8 }}>
                ⚠ AI Flagged Discrepancies ({humanReview.flagged_steps.length})
              </div>
              {humanReview.flagged_steps.map(step => (
                <div key={step.step_id} style={{
                  padding: '8px 12px', marginBottom: 6,
                  background: 'var(--sorry-b-bg)', borderRadius: 'var(--radius-sm)',
                  borderLeft: '3px solid var(--sorry-b)',
                  fontSize: 12, color: 'var(--text-secondary)',
                }}>
                  <strong>Step {step.step_id}:</strong> {step.discrepancy}
                </div>
              ))}
            </div>
          )}

          {/* Decision buttons */}
          <div style={{ display: 'flex', gap: 12, justifyContent: 'flex-end', paddingTop: 8, borderTop: '1px solid var(--border)' }}>
            <button
              className="btn"
              onClick={() => sendDecision('abort')}
              style={{ background: 'var(--sorry-a-bg)', color: 'var(--sorry-a)', border: '1px solid var(--sorry-a)' }}
            >
              Abort Audit
            </button>
            <button
              className="btn"
              onClick={() => sendDecision('retry')}
              style={{ background: 'var(--sorry-b-bg)', color: 'var(--sorry-b)', border: '1px solid var(--sorry-b)' }}
            >
              Retry Translation
            </button>
            <button
              className="btn btn-primary"
              onClick={() => sendDecision('approve')}
            >
              Approve & Continue
            </button>
          </div>
        </div>
      )}

      {/* Result link */}
      {isComplete && resultFile && (
        <div className="card animate-fade-in" style={{
          background: 'var(--sorry-b-bg)',
          borderColor: 'var(--sorry-b)',
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <span style={{ fontSize: 20 }}>✅</span>
            <div>
              <div style={{ fontWeight: 600, marginBottom: 4 }}>Audit Complete</div>
              <div style={{ fontSize: 13, color: 'var(--text-secondary)' }}>
                Report generated successfully.
              </div>
            </div>
            <button
              className="btn btn-primary"
              style={{ marginLeft: 'auto' }}
              onClick={() => navigate(`/report/${resultFile}`)}
            >
              View Report →
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
