/**
 * Proof Auditor UI Server — Configuration API
 * Reads and writes API credentials directly to local .env
 */
import type { FastifyInstance } from 'fastify';
import fs from 'fs';
import path from 'path';

export interface EnvConfig {
  provider: string;
  model: string;
  openai_key: string;
  openai_base: string;
  anthropic_key: string;
  anthropic_base: string;
  gemini_key: string;
  openrouter_key: string;
}

export function register(fastify: FastifyInstance, projectPath: string) {
  const envPath = path.join(projectPath, '.env');

  // Helper to parse .env into an object safely
  function readEnv(): Record<string, string> {
    const config: Record<string, string> = {};
    if (!fs.existsSync(envPath)) return config;
    
    const content = fs.readFileSync(envPath, 'utf-8');
    const lines = content.split('\n');
    for (const line of lines) {
      if (line.trim() && !line.startsWith('#') && line.includes('=')) {
        const idx = line.indexOf('=');
        const key = line.substring(0, idx).trim();
        let val = line.substring(idx + 1).trim();
        // Remove quotes if present
        if ((val.startsWith('"') && val.endsWith('"')) || (val.startsWith("'") && val.endsWith("'"))) {
          val = val.substring(1, val.length - 1);
        }
        config[key] = val;
      }
    }
    return config;
  }

  // Helper to rewrite .env preserving structure, or appending if missing
  function writeConfig(updates: Record<string, string>) {
    let lines: string[] = [];
    if (fs.existsSync(envPath)) {
      lines = fs.readFileSync(envPath, 'utf-8').split('\n');
    }

    const updatedKeys = new Set<string>();

    // Update existing
    for (let i = 0; i < lines.length; i++) {
      const line = lines[i];
      if (line.trim() && !line.startsWith('#') && line.includes('=')) {
        const idx = line.indexOf('=');
        const key = line.substring(0, idx).trim();
        if (updates.hasOwnProperty(key)) {
          lines[i] = `${key}=${updates[key]}`;
          updatedKeys.add(key);
        }
      }
    }

    // Append new
    for (const [key, value] of Object.entries(updates)) {
      if (!updatedKeys.has(key)) {
        if (lines.length > 0 && lines[lines.length - 1] !== '') {
          lines.push('');
        }
        lines.push(`${key}=${value}`);
      }
    }

    fs.writeFileSync(envPath, lines.join('\n'));
  }

  /** GET /api/config — Read config from .env */
  fastify.get('/api/config', async (req, reply) => {
    const env = readEnv();
    
    // We try to grab anything starting with sk- or whatever is present
    return {
      provider: env['PA_UI_PROVIDER'] || 'openai',
      model: env['PA_UI_MODEL'] || '',
      openai_key: env['OPENAI_API_KEY'] || '',
      openai_base: env['OPENAI_BASE_URL'] || '',
      anthropic_key: env['ANTHROPIC_API_KEY'] || '',
      anthropic_base: env['ANTHROPIC_BASE_URL'] || '',
      gemini_key: env['GEMINI_API_KEY'] || '',
      openrouter_key: env['OPENROUTER_API_KEY'] || '',
      env_path: envPath,
    };
  });

  /** POST /api/config — Update .env */
  fastify.post<{ Body: Partial<EnvConfig> }>('/api/config', async (req, reply) => {
    const body = req.body;
    const updates: Record<string, string> = {};

    if (body.provider !== undefined) updates['PA_UI_PROVIDER'] = body.provider;
    if (body.model !== undefined) updates['PA_UI_MODEL'] = body.model;
    if (body.openai_key !== undefined) updates['OPENAI_API_KEY'] = body.openai_key;
    if (body.openai_base !== undefined) updates['OPENAI_BASE_URL'] = body.openai_base;
    if (body.anthropic_key !== undefined) updates['ANTHROPIC_API_KEY'] = body.anthropic_key;
    if (body.anthropic_base !== undefined) updates['ANTHROPIC_BASE_URL'] = body.anthropic_base;
    if (body.gemini_key !== undefined) updates['GEMINI_API_KEY'] = body.gemini_key;
    if (body.openrouter_key !== undefined) updates['OPENROUTER_API_KEY'] = body.openrouter_key;

    writeConfig(updates);
    return { success: true };
  });

  /** GET /api/config/test — Test API connectivity */
  fastify.get('/api/config/test', async (req, reply) => {
    const env = readEnv();
    const provider = env['PA_UI_PROVIDER'] || 'openai';
    let baseUrl = '';
    let apiKey = '';

    if (provider === 'openai') {
      baseUrl = env['OPENAI_BASE_URL'] || 'https://api.openai.com/v1';
      apiKey = env['OPENAI_API_KEY'] || '';
    } else if (provider === 'anthropic') {
      baseUrl = env['ANTHROPIC_BASE_URL'] || 'https://api.anthropic.com';
      apiKey = env['ANTHROPIC_API_KEY'] || '';
    } else if (provider === 'gemini') {
      apiKey = env['GEMINI_API_KEY'] || '';
      baseUrl = 'https://generativelanguage.googleapis.com';
    } else if (provider === 'openrouter') {
      baseUrl = 'https://openrouter.ai/api/v1';
      apiKey = env['OPENROUTER_API_KEY'] || '';
    }

    if (!apiKey) {
      return { success: false, error: 'No API key configured for ' + provider };
    }

    try {
      // For OpenAI-compatible, list models as a lightweight ping
      const testUrl = provider === 'anthropic' 
        ? baseUrl + '/v1/messages'
        : baseUrl + '/models';
      
      const headers: Record<string, string> = {};
      if (provider === 'anthropic') {
        headers['x-api-key'] = apiKey;
        headers['anthropic-version'] = '2023-06-01';
      } else {
        headers['Authorization'] = `Bearer ${apiKey}`;
      }

      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 5000);
      
      const res = await fetch(testUrl, {
        method: 'GET',
        headers,
        signal: controller.signal,
      });
      clearTimeout(timeout);

      if (res.ok || res.status === 405) {
        // 405 = Method Not Allowed is acceptable for Anthropic (it expects POST)
        return { success: true, message: `${provider} API reachable (HTTP ${res.status})` };
      } else {
        const body = await res.text().catch(() => '');
        return { success: false, error: `HTTP ${res.status}: ${body.slice(0, 200)}` };
      }
    } catch (e: any) {
      return { success: false, error: e.message || 'Connection failed' };
    }
  });
}
