/**
 * Proof Auditor UI Server — Status & Benchmark API
 */
import type { FastifyInstance } from 'fastify';
import fs from 'fs';
import path from 'path';

export function register(fastify: FastifyInstance, projectPath: string) {
  /** GET /api/status — project status & API key availability */
  fastify.get('/api/status', async () => {
    // Read .env to check configured providers
    const envPath = path.join(projectPath, '.env');
    const providers: Record<string, boolean> = {
      openai: false,
      anthropic: false,
      gemini: false,
      openrouter: false,
    };

    if (fs.existsSync(envPath)) {
      const envContent = fs.readFileSync(envPath, 'utf-8');
      // Parse .env properly (skip comments and empty lines)
      const envVars: Record<string, string> = {};
      for (const line of envContent.split('\n')) {
        if (line.trim() && !line.startsWith('#') && line.includes('=')) {
          const idx = line.indexOf('=');
          const key = line.substring(0, idx).trim();
          const val = line.substring(idx + 1).trim();
          envVars[key] = val;
        }
      }
      if (envVars['OPENAI_API_KEY']) providers.openai = true;
      if (envVars['ANTHROPIC_API_KEY']) providers.anthropic = true;
      if (envVars['GEMINI_API_KEY']) providers.gemini = true;
      if (envVars['OPENROUTER_API_KEY']) providers.openrouter = true;
    }

    // Check for benchmark data
    const benchmarkPath = path.join(projectPath, 'benchmark', 'expected_results.json');
    const hasBenchmark = fs.existsSync(benchmarkPath);

    // Count existing reports
    const reportsDir = path.join(projectPath, 'reports');
    let reportCount = 0;
    if (fs.existsSync(reportsDir)) {
      reportCount = fs.readdirSync(reportsDir).filter(f => f.endsWith('.json')).length;
    }

    // Count benchmark proofs
    const phase0Dir = path.join(projectPath, 'benchmark', 'phase0');
    let proofFiles: string[] = [];
    if (fs.existsSync(phase0Dir)) {
      proofFiles = fs.readdirSync(phase0Dir).filter(f => f.endsWith('.txt'));
    }

    return {
      project: 'proof-auditor',
      version: '0.1.0',
      path: projectPath,
      providers,
      report_count: reportCount,
      has_benchmark: hasBenchmark,
      proof_files: proofFiles,
    };
  });

  /** GET /api/benchmark — expected benchmark results */
  fastify.get('/api/benchmark', async (_, reply) => {
    const benchPath = path.join(projectPath, 'benchmark', 'expected_results.json');
    if (!fs.existsSync(benchPath)) {
      return reply.status(404).send({ error: 'No benchmark data found' });
    }
    try {
      return JSON.parse(fs.readFileSync(benchPath, 'utf-8'));
    } catch (e: any) {
      return reply.status(500).send({ error: e.message });
    }
  });

  /** GET /api/benchmark/proofs — list available benchmark proofs */
  fastify.get('/api/benchmark/proofs', async () => {
    const dirs = ['phase0', 'known_buggy', 'known_correct'];
    const result: Array<{ name: string; category: string; content: string }> = [];

    for (const dir of dirs) {
      const dirPath = path.join(projectPath, 'benchmark', dir);
      if (!fs.existsSync(dirPath)) continue;
      const files = fs.readdirSync(dirPath).filter(f => f.endsWith('.txt'));
      for (const f of files) {
        result.push({
          name: f.replace('.txt', ''),
          category: dir,
          content: fs.readFileSync(path.join(dirPath, f), 'utf-8'),
        });
      }
    }
    return result;
  });
}
