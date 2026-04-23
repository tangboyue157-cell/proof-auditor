/**
 * Proof Auditor UI Server — Reports API
 *
 * Reads audit reports from the project's reports/ directory.
 */
import type { FastifyInstance } from 'fastify';
import fs from 'fs';
import path from 'path';

export function register(fastify: FastifyInstance, projectPath: string) {
  const reportsDir = path.join(projectPath, 'reports');

  /** GET /api/reports — list all audit report files */
  fastify.get('/api/reports', async () => {
    if (!fs.existsSync(reportsDir)) return [];

    const files = fs.readdirSync(reportsDir)
      .filter(f => f.endsWith('.json'))
      .map(f => {
        const filePath = path.join(reportsDir, f);
        const stat = fs.statSync(filePath);
        let data: any = {};
        try {
          data = JSON.parse(fs.readFileSync(filePath, 'utf-8'));
        } catch { /* skip parse errors */ }
        return {
          name: f.replace('.json', ''),
          file: f,
          proof_title: data.proof_title || f.replace('.json', ''),
          verdict: data.verdict || 'UNKNOWN',
          total_sorrys: data.total_sorrys || 0,
          fidelity_score: data.back_translation?.fidelity_score ?? null,
          modified: stat.mtime.toISOString(),
        };
      });

    return files.sort((a, b) => b.modified.localeCompare(a.modified));
  });

  /** GET /api/reports/:name — get full report content */
  fastify.get<{ Params: { name: string } }>('/api/reports/:name', async (req, reply) => {
    const { name } = req.params;
    // Support both with and without .json extension
    const fileName = name.endsWith('.json') ? name : `${name}.json`;
    const filePath = path.join(reportsDir, fileName);

    if (!fs.existsSync(filePath)) {
      return reply.status(404).send({ error: `Report not found: ${fileName}` });
    }

    try {
      const data = JSON.parse(fs.readFileSync(filePath, 'utf-8'));
      return data;
    } catch (e: any) {
      return reply.status(500).send({ error: `Failed to parse report: ${e.message}` });
    }
  });
}
