/**
 * Proof Auditor UI Server — entry point
 *
 * Composes route modules and starts Fastify.
 */
import Fastify from 'fastify';
import cors from '@fastify/cors';
import staticFiles from '@fastify/static';
import websocket from '@fastify/websocket';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

// Route modules
import { register as registerReports } from './routes/reports.js';
import { register as registerStatus } from './routes/status.js';
import { register as registerAudit } from './routes/audit.js';
import { register as registerStream } from './routes/stream.js';
import { register as registerConfig } from './routes/config.js';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

function parseArgs(): { projectPath: string; port: number } {
  const args = process.argv.slice(2);
  // Default: proof-auditor root is two levels up from server/src/
  let projectPath = path.resolve(__dirname, '..', '..', '..');
  let port = 3000;
  for (let i = 0; i < args.length; i++) {
    if (args[i] === '--project' && i + 1 < args.length) projectPath = path.resolve(args[++i]);
    else if (args[i] === '--port' && i + 1 < args.length) port = parseInt(args[++i], 10);
  }
  return { projectPath, port };
}

export async function createServer(options: { projectPath: string; port: number }) {
  const { projectPath, port } = options;

  const fastify = Fastify({ logger: false, bodyLimit: 50 * 1024 * 1024 /* 50 MB for PDF uploads */ });
  await fastify.register(cors);
  await fastify.register(websocket);

  // Serve built client (SPA)
  const clientBuildPath = path.join(__dirname, '../../client/dist');
  if (fs.existsSync(clientBuildPath)) {
    await fastify.register(staticFiles, { root: clientBuildPath, prefix: '/' });
    fastify.setNotFoundHandler((req, reply) => {
      if (req.url.startsWith('/api/')) return reply.status(404).send({ error: 'Not found' });
      return reply.sendFile('index.html');
    });
  }

  // Register route modules
  registerReports(fastify, projectPath);
  registerStatus(fastify, projectPath);
  registerAudit(fastify, projectPath);
  registerStream(fastify);
  registerConfig(fastify, projectPath);

  await fastify.listen({ port, host: '0.0.0.0' });
  return fastify;
}

// CLI entry point
if (import.meta.url === `file://${process.argv[1]}`) {
  const { projectPath, port } = parseArgs();
  console.log(`\n  🔬 Proof Auditor UI`);
  console.log(`  ───────────────────`);
  console.log(`  → http://localhost:${port}`);
  console.log(`  → Project: ${projectPath}\n`);
  createServer({ projectPath, port }).catch(err => { console.error(err); process.exit(1); });
}
