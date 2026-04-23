/**
 * WebSocket hook for real-time audit log streaming
 * 
 * Optimizations:
 * - Batched rendering via requestAnimationFrame to avoid 100+ re-renders/sec
 * - useRef for isComplete to avoid stale closure in onclose
 * 
 * Human Review:
 * - Detects __HUMAN_REVIEW__ signal lines and exposes parsed review data
 */
import { useState, useEffect, useRef, useCallback } from 'react';

interface StreamMessage {
  type: 'log' | 'complete' | 'error';
  data?: string;
  status?: string;
  result_file?: string;
  message?: string;
}

export interface HumanReviewData {
  original_proof: string;
  back_translated_text: string;
  fidelity_score: number;
  overall_match: boolean;
  flagged_steps: { step_id: number; discrepancy: string }[];
  attempt: number;
}

export function useAuditStream(auditId: string | null) {
  const [lines, setLines] = useState<string[]>([]);
  const [isComplete, setIsComplete] = useState(false);
  const [finalStatus, setFinalStatus] = useState<string | null>(null);
  const [resultFile, setResultFile] = useState<string | null>(null);
  const [humanReview, setHumanReview] = useState<HumanReviewData | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const isCompleteRef = useRef(false); // P0 #2: avoid stale closure
  const bufferRef = useRef<string[]>([]); // P1 #4: batch buffer
  const rafRef = useRef<number | null>(null);

  // Keep ref in sync with state
  useEffect(() => {
    isCompleteRef.current = isComplete;
  }, [isComplete]);

  const reset = useCallback(() => {
    setLines([]);
    setIsComplete(false);
    isCompleteRef.current = false;
    setFinalStatus(null);
    setResultFile(null);
    setHumanReview(null);
    bufferRef.current = [];
  }, []);

  // P1 #4: Flush buffer into React state at ~60fps max
  const flushBuffer = useCallback(() => {
    if (bufferRef.current.length > 0) {
      const batch = bufferRef.current.splice(0);
      setLines(prev => [...prev, ...batch]);
    }
    rafRef.current = null;
  }, []);

  const scheduleFlush = useCallback(() => {
    if (rafRef.current === null) {
      rafRef.current = requestAnimationFrame(flushBuffer);
    }
  }, [flushBuffer]);

  useEffect(() => {
    if (!auditId) return;

    reset();

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/api/audit-stream/${auditId}`;
    const ws = new WebSocket(wsUrl);
    wsRef.current = ws;

    ws.onmessage = (event) => {
      try {
        const msg: StreamMessage = JSON.parse(event.data);
        switch (msg.type) {
          case 'log':
            if (msg.data) {
              // Detect __HUMAN_REVIEW__ signal
              if (msg.data.startsWith('__HUMAN_REVIEW__:')) {
                try {
                  const jsonStr = msg.data.slice('__HUMAN_REVIEW__:'.length);
                  const reviewData: HumanReviewData = JSON.parse(jsonStr);
                  setHumanReview(reviewData);
                } catch {
                  // Failed to parse review data, show as regular log
                  bufferRef.current.push(msg.data);
                  scheduleFlush();
                }
              } else {
                bufferRef.current.push(msg.data);
                scheduleFlush();
              }
            }
            break;
          case 'complete':
            // Flush any remaining buffer immediately
            if (bufferRef.current.length > 0) {
              const batch = bufferRef.current.splice(0);
              setLines(prev => [...prev, ...batch]);
            }
            setIsComplete(true);
            setHumanReview(null); // Clear review panel on completion
            setFinalStatus(msg.status || 'done');
            setResultFile(msg.result_file || null);
            break;
          case 'error':
            if (bufferRef.current.length > 0) {
              const batch = bufferRef.current.splice(0);
              setLines(prev => [...prev, ...batch]);
            }
            setIsComplete(true);
            setHumanReview(null);
            setFinalStatus('error');
            if (msg.message) {
              setLines(prev => [...prev, `Error: ${msg.message}`]);
            }
            break;
        }
      } catch {
        // Non-JSON message, add as raw line
        bufferRef.current.push(event.data);
        scheduleFlush();
      }
    };

    ws.onerror = () => {
      setLines(prev => [...prev, '⚠ WebSocket connection error']);
    };

    ws.onclose = () => {
      // P0 #2: use ref instead of stale closure
      if (!isCompleteRef.current) {
        // Fallback: if WS closes without complete message, poll status
        setIsComplete(true);
      }
    };

    return () => {
      ws.close();
      wsRef.current = null;
      if (rafRef.current !== null) {
        cancelAnimationFrame(rafRef.current);
        rafRef.current = null;
      }
    };
  }, [auditId]); // eslint-disable-line react-hooks/exhaustive-deps

  return { lines, isComplete, finalStatus, resultFile, humanReview, setHumanReview, reset };
}
