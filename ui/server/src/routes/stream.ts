/**
 * Proof Auditor UI Server — WebSocket audit stream
 */
import type { FastifyInstance } from 'fastify';
import { jobs } from './audit.js';

export function register(fastify: FastifyInstance) {
  /** WebSocket /api/audit-stream/:id — real-time audit log */
  fastify.get<{ Params: { id: string } }>('/api/audit-stream/:id', { websocket: true }, (socket, req) => {
    const job = jobs.get(req.params.id);

    if (!job) {
      socket.send(JSON.stringify({ type: 'error', message: 'Job not found' }));
      socket.close();
      return;
    }

    // Send existing output first (replay)
    for (const line of job.output) {
      socket.send(JSON.stringify({ type: 'log', data: line }));
    }

    // If already complete, send final status
    if (job.status === 'done' || job.status === 'error') {
      socket.send(JSON.stringify({
        type: 'complete',
        status: job.status,
        result_file: job.result_file,
      }));
      socket.close();
      return;
    }

    // Subscribe to live updates
    const listener = (line: string) => {
      try {
        if (line.startsWith('__DONE__:') || line.startsWith('__ERROR__:')) {
          socket.send(JSON.stringify({
            type: 'complete',
            status: job.status,
            result_file: job.result_file,
          }));
          socket.close();
        } else {
          socket.send(JSON.stringify({ type: 'log', data: line }));
        }
      } catch {
        // Socket might be closed
        job.listeners.delete(listener);
      }
    };

    job.listeners.add(listener);

    socket.on('close', () => {
      job.listeners.delete(listener);
    });
  });
}
