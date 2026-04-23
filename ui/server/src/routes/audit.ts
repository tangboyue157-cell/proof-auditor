/**
 * Proof Auditor UI Server — Audit Execution API
 *
 * Launches Python audit scripts via child_process and tracks status.
 */
import type { FastifyInstance } from 'fastify';
import { spawn, ChildProcess } from 'child_process';
import { randomUUID } from 'crypto';
import fs from 'fs';
import path from 'path';
import type { AuditJob } from '../types.js';

const jobs = new Map<string, AuditJob & { process?: ChildProcess; listeners: Set<(line: string) => void> }>();

export function register(fastify: FastifyInstance, projectPath: string) {
  /** POST /api/audit — start a new audit */
  fastify.post<{
    Body: { 
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
    };
  }>('/api/audit', {
    config: {
      // Allow large body for PDF uploads (default is 1MB, PDFs can be much larger)
    },
  }, async (req, reply) => {
    const { 
      proof_text, mode = 'auto', proof_name, provider, api_base, api_key, model_name,
      pdf_base64, pdf_filename, pdf_backend, pdf_pages, pdf_theorem,
      ref_pdfs,
    } = req.body || {};

    const isPdf = !!pdf_base64;

    if (!isPdf && (!proof_text || typeof proof_text !== 'string' || proof_text.trim().length === 0)) {
      return reply.status(400).send({ error: 'proof_text or pdf_base64 is required' });
    }

    const id = randomUUID().slice(0, 8);
    const name = proof_name || `web_audit_${id}`;

    // Write proof to a temp file
    const tmpDir = path.join(projectPath, 'ProofAuditor', 'Workspace');
    fs.mkdirSync(tmpDir, { recursive: true });

    let proofFile: string;

    if (isPdf) {
      // Decode base64 PDF and write as binary file
      const pdfBuffer = Buffer.from(pdf_base64!, 'base64');
      const pdfName = pdf_filename || `${name}.pdf`;
      proofFile = path.join(tmpDir, pdfName);
      fs.writeFileSync(proofFile, pdfBuffer);
    } else {
      proofFile = path.join(tmpDir, `${name}.txt`);
      fs.writeFileSync(proofFile, proof_text!);
    }

    const job: AuditJob & { process?: ChildProcess; listeners: Set<(line: string) => void> } = {
      id,
      status: 'running',
      proof_title: name,
      started_at: new Date().toISOString(),
      output: [],
      listeners: new Set(),
    };
    jobs.set(id, job);

    // Find python binary
    const venvPython = path.join(projectPath, '.venv', 'bin', 'python');
    const python = fs.existsSync(venvPython) ? venvPython : 'python3';
    const scriptPath = path.join(projectPath, 'scripts', 'audit.py');

    // Build environment overrides based on the selected provider
    const envOverrides: Record<string, string> = { 
      PYTHONPATH: projectPath,
      PYTHONUNBUFFERED: '1' // Force real-time streaming to the UI
    };
    if (api_key) {
      if (provider === 'anthropic') envOverrides['ANTHROPIC_API_KEY'] = api_key;
      else if (provider === 'gemini' || provider === 'google') envOverrides['GEMINI_API_KEY'] = api_key;
      else if (provider === 'openrouter') envOverrides['OPENROUTER_API_KEY'] = api_key;
      else envOverrides['OPENAI_API_KEY'] = api_key; // default fallback
    }
    if (api_base) {
      envOverrides['OPENAI_BASE_URL'] = api_base; // AIClient respects this for OpenAI compatible
      envOverrides['ANTHROPIC_BASE_URL'] = api_base;
    }
    // Also pass the provider hint and model name if the underlying ai_client.py is updated to check it
    if (provider) envOverrides['PA_UI_PROVIDER'] = provider;
    if (model_name) envOverrides['PA_UI_MODEL'] = model_name;
    envOverrides['PA_AUDIT_ID'] = id;  // For human review IPC

    // Build command arguments
    const args = [scriptPath, proofFile, '--mode', mode];
    if (isPdf) {
      if (pdf_backend) args.push('--backend', pdf_backend);
      if (pdf_pages) args.push('--pages', pdf_pages);
      if (pdf_theorem) args.push('--theorem', pdf_theorem);
    }

    // Handle reference documents
    if (ref_pdfs && Array.isArray(ref_pdfs) && ref_pdfs.length > 0) {
      const refsDir = path.join(tmpDir, 'refs');
      fs.mkdirSync(refsDir, { recursive: true });
      const refPaths: string[] = [];
      for (const ref of ref_pdfs) {
        if (ref.base64 && ref.filename) {
          const refBuffer = Buffer.from(ref.base64, 'base64');
          const refFile = path.join(refsDir, ref.filename);
          fs.writeFileSync(refFile, refBuffer);
          refPaths.push(refFile);
        }
      }
      if (refPaths.length > 0) {
        args.push('--refs', ...refPaths);
      }
    }

    const proc = spawn(python, args, {
      cwd: projectPath,
      env: { ...process.env, ...envOverrides },
      stdio: ['ignore', 'pipe', 'pipe'],
    });

    job.process = proc;
    job.status = 'running';  // Set status explicitly to running so decision API allows posts

    const handleData = (chunk: Buffer) => {
      const lines = chunk.toString().split('\n').filter(Boolean);
      for (const line of lines) {
        job.output.push(line);
        // Notify WebSocket listeners
        for (const cb of job.listeners) cb(line);
      }
    };

    proc.stdout?.on('data', handleData);
    proc.stderr?.on('data', handleData);

    proc.on('close', (code) => {
      job.status = code === 0 ? 'done' : 'error';
      // Check if report was generated
      const reportFile = path.join(projectPath, 'reports', `audit_${name}.json`);
      if (fs.existsSync(reportFile)) {
        job.result_file = `audit_${name}`;
      }
      // Notify listeners of completion
      for (const cb of job.listeners) cb(`__DONE__:${job.status}`);
    });

    proc.on('error', (err) => {
      job.status = 'error';
      job.output.push(`Process error: ${err.message}`);
      for (const cb of job.listeners) cb(`__ERROR__:${err.message}`);
    });

    return { audit_id: id, status: 'running' };
  });

  /** GET /api/audit/:id — get audit job status */
  fastify.get<{ Params: { id: string } }>('/api/audit/:id', async (req, reply) => {
    const job = jobs.get(req.params.id);
    if (!job) return reply.status(404).send({ error: 'Job not found' });

    return {
      id: job.id,
      status: job.status,
      proof_title: job.proof_title,
      started_at: job.started_at,
      output_lines: job.output.length,
      result_file: job.result_file,
    };
  });

  /** GET /api/audit/:id/output — get full output log */
  fastify.get<{ Params: { id: string } }>('/api/audit/:id/output', async (req, reply) => {
    const job = jobs.get(req.params.id);
    if (!job) return reply.status(404).send({ error: 'Job not found' });
    return { output: job.output };
  });

  /** POST /api/audit/:id/decision — send human review decision to a running audit */
  fastify.post<{ Params: { id: string }; Body: { action: string; feedback?: string } }>(
    '/api/audit/:id/decision',
    async (req, reply) => {
      const job = jobs.get(req.params.id);
      if (!job) return reply.status(404).send({ error: 'Job not found' });

      const { action, feedback } = req.body || {};
      if (!action || !['approve', 'retry', 'abort'].includes(action)) {
        return reply.status(400).send({ error: 'action must be approve, retry, or abort' });
      }

      // Write decision file for Python to poll
      const decisionDir = path.join(projectPath, 'ProofAuditor', 'Workspace');
      fs.mkdirSync(decisionDir, { recursive: true });
      const decisionFile = path.join(decisionDir, `.decision_${req.params.id}.json`);
      fs.writeFileSync(decisionFile, JSON.stringify({ action, feedback: feedback || '' }));

      return { success: true };
    }
  );

  /** DELETE /api/audit/:id — cancel a running audit */
  fastify.delete<{ Params: { id: string } }>('/api/audit/:id', async (req, reply) => {
    const job = jobs.get(req.params.id);
    if (!job) return reply.status(404).send({ error: 'Job not found' });

    if (job.status !== 'running' || !job.process) {
      return reply.status(400).send({ error: 'Job is not running' });
    }

    // Kill the process tree
    try {
      job.process.kill('SIGTERM');
      // Give it 2 seconds, then force kill
      setTimeout(() => {
        try { job.process?.kill('SIGKILL'); } catch {}
      }, 2000);
    } catch (e: any) {
      // Process might already be dead
    }

    job.status = 'error';
    job.output.push('⛔ Audit cancelled by user');
    for (const cb of job.listeners) cb('__DONE__:error');

    return { success: true, message: 'Audit cancelled' };
  });
}

/** Expose the jobs map for WebSocket route */
export { jobs };
