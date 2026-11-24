import test from 'node:test';
import assert from 'node:assert/strict';
import path from 'node:path';
import os from 'node:os';
import { mkdtemp, readdir, readFile } from 'node:fs/promises';
import { createApp } from '../apps/control-api/src/app.js';
import { mockProtocolAllowed } from '../apps/control-api/src/http-guards.js';
import { openDatabase } from '../packages/database/index.js';
import { expectedSchemaVersions } from '../packages/database/transfer.js';
import { issueTestInvite } from './helpers/invitations.js';
import { defaultConfig } from '../packages/contracts/index.js';

const ROOT = path.resolve('.');
const SCAN_DIRS = [
  'packages',
  'adapters',
  'apps/control-api/src',
  'apps/node-agent/src',
  'apps/web/src',
];

async function collectTsFiles(dir: string, out: string[] = []): Promise<string[]> {
  const entries = await readdir(dir, { withFileTypes: true }).catch(() => []);
  for (const entry of entries) {
    if (entry.name === 'node_modules' || entry.name === 'dist') continue;
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) await collectTsFiles(full, out);
    else if (entry.name.endsWith('.ts') || entry.name.endsWith('.tsx')) out.push(path.resolve(full));
  }
  return out;
}

const IMPORT_RE = /(?:import|export)\s+(?:[^'"]*?\s+from\s+)?['"]([^'"]+)['"]/g;

function resolveSpec(src: string, spec: string, known: Set<string>): string | null {
  if (spec.endsWith('.css') || spec.endsWith('.json') || spec.endsWith('.svg') || spec.endsWith('.png')) return null;
  if (spec.startsWith('.')) {
    const base = path.resolve(path.dirname(src), spec);
    const candidates = [base, `${base}.ts`, `${base}.tsx`, path.join(base, 'index.ts'), path.join(base, 'index.tsx')];
    if (spec.endsWith('.js')) {
      const stem = path.resolve(path.dirname(src), spec.slice(0, -3));
      candidates.unshift(`${stem}.ts`, `${stem}.tsx`);
    }
    for (const c of candidates) if (known.has(c)) return c;
  }
  return null;
}

function findSccs(adj: Map<string, Set<string>>): string[][] {
  let index = 0;
  const stack: string[] = [];
  const onStack = new Set<string>();
  const disc = new Map<string, number>();
  const low = new Map<string, number>();
  const sccs: string[][] = [];
  function dfs(v: string) {
    disc.set(v, index);
    low.set(v, index);
    index++;
    stack.push(v);
    onStack.add(v);
    for (const w of adj.get(v) || []) {
      if (!disc.has(w)) {
        dfs(w);
        low.set(v, Math.min(low.get(v)!, low.get(w)!));
      } else if (onStack.has(w)) {
        low.set(v, Math.min(low.get(v)!, disc.get(w)!));
      }
    }
    if (low.get(v) === disc.get(v)) {
      const comp: string[] = [];
      while (true) {
        const w = stack.pop()!;
        onStack.delete(w);
        comp.push(path.relative(ROOT, w).replace(/\\/g, '/'));
        if (w === v) break;
      }
      if (comp.length > 1) sccs.push(comp.sort());
    }
  }
  for (const v of adj.keys()) if (!disc.has(v)) dfs(v);
  return sccs;
}

test('TypeScript subsystem boundaries: zero cycles and zero layer inversions', async () => {
  const files: string[] = [];
  for (const d of SCAN_DIRS) await collectTsFiles(path.join(ROOT, d), files);
  const known = new Set(files);
  const adj = new Map<string, Set<string>>();
  const violations: string[] = [];

  for (const file of files) {
    const rel = path.relative(ROOT, file).replace(/\\/g, '/');
    const text = await readFile(file, 'utf8');
    const edges = new Set<string>();
    for (const match of text.matchAll(IMPORT_RE)) {
      const target = resolveSpec(file, match[1], known);
      if (!target || target === file) continue;
      edges.add(target);
      const targetRel = path.relative(ROOT, target).replace(/\\/g, '/');

      if (rel.startsWith('adapters/') && targetRel.startsWith('apps/')) {
        violations.push(`adapter->app inversion: ${rel} -> ${targetRel}`);
      }
      if (rel.startsWith('packages/') && (targetRel.startsWith('apps/') || targetRel.startsWith('adapters/'))) {
        violations.push(`package->app/adapter inversion: ${rel} -> ${targetRel}`);
      }
      if (rel.includes('/media/') && targetRel.includes('/bilibili/')) {
        violations.push(`media->bilibili inversion: ${rel} -> ${targetRel}`);
      }
      if (rel.startsWith('adapters/protocol/') && targetRel.startsWith('adapters/astrbot/')) {
        violations.push(`protocol->ai inversion: ${rel} -> ${targetRel}`);
      }
      if (rel === 'apps/node-agent/src/dsh-host.ts' && targetRel === 'apps/node-agent/src/agent.ts') {
        violations.push(`dsh-host->NodeAgent coupling: ${rel} -> ${targetRel}`);
      }
      if (
        (rel === 'apps/control-api/src/behavior-data.ts' || rel === 'apps/control-api/src/model-catalog.ts') &&
        targetRel === 'apps/node-agent/src/agent.ts'
      ) {
        violations.push(`control-api route->NodeAgent coupling: ${rel} -> ${targetRel}`);
      }
    }
    adj.set(file, edges);
  }

  assert.deepEqual(findSccs(adj), [], 'TypeScript import graph must have zero cycles');
  assert.deepEqual(violations, [], `Architecture boundary violations:\n${violations.join('\n')}`);
});

test('Control API in externalAgent mode owns zero NodeAgent instances', async t => {
  const root = await mkdtemp(path.join(os.tmpdir(), 'fishbot-ext-agent-'));
  const { app, db, agent } = await createApp({ root, externalAgent: true, agent: false });
  t.after(() => app.close());

  assert.equal(agent, null, 'externalAgent mode must not instantiate NodeAgent inside Control API');

  const reg = await app.inject({
    method: 'POST',
    url: '/v1/auth/register',
    payload: {
      inviteCode: await issueTestInvite(db),
      email: 'ext@example.com',
      password: 'test-password-123',
      name: 'ext',
    },
  });
  assert.equal(reg.statusCode, 200, reg.body);
  const cookie = reg.cookies[0].value;

  const created = await app.inject({
    method: 'POST',
    url: '/v1/bots',
    cookies: { fish_session: cookie },
    payload: { name: 'ExternalModeBot', protocol: 'napcat', config: defaultConfig },
  });
  assert.equal(created.statusCode, 201, created.body);
  const botId = created.json().id;

  const detail = await app.inject({
    method: 'GET',
    url: `/v1/bots/${botId}`,
    cookies: { fish_session: cookie },
  });
  assert.equal(detail.statusCode, 200, detail.body);
  assert.ok(detail.json().capabilities.includes('onebot-reverse-ws'));
});

test('Mock protocol is blocked in production unless explicitly enabled', () => {
  const prevEnv = process.env.NODE_ENV;
  const prevAllow = process.env.FISHBOT_ALLOW_MOCK_PROTOCOL;
  try {
    process.env.NODE_ENV = 'production';
    delete process.env.FISHBOT_ALLOW_MOCK_PROTOCOL;
    assert.equal(mockProtocolAllowed(), false);

    process.env.FISHBOT_ALLOW_MOCK_PROTOCOL = 'true';
    assert.equal(mockProtocolAllowed(), true);

    process.env.NODE_ENV = 'test';
    delete process.env.FISHBOT_ALLOW_MOCK_PROTOCOL;
    assert.equal(mockProtocolAllowed(), true);
  } finally {
    if (prevEnv === undefined) delete process.env.NODE_ENV;
    else process.env.NODE_ENV = prevEnv;
    if (prevAllow === undefined) delete process.env.FISHBOT_ALLOW_MOCK_PROTOCOL;
    else process.env.FISHBOT_ALLOW_MOCK_PROTOCOL = prevAllow;
  }
});

test('PG migration 021 retires dead autonomy columns cleanly', async t => {
  const root = await mkdtemp(path.join(os.tmpdir(), 'fishbot-mig021-'));
  const db = await openDatabase(root, '');
  t.after(() => db.close());

  const versions = (await db.query('SELECT version FROM schema_migrations ORDER BY version')).map((r: any) => r.version);
  assert.deepEqual(versions, expectedSchemaVersions);
  assert.ok(versions.includes('021'));

  const settingsCols = (
    await db.query("SELECT column_name FROM information_schema.columns WHERE table_name='autonomy_settings'")
  ).map((r: any) => r.column_name);
  for (const dead of ['context_cycle_seq', 'context_checkpoint', 'summary', 'wait']) {
    assert.ok(!settingsCols.includes(dead), `autonomy_settings must not contain retired column ${dead}`);
  }

  const cycleCols = (
    await db.query("SELECT column_name FROM information_schema.columns WHERE table_name='autonomy_cycles'")
  ).map((r: any) => r.column_name);
  assert.ok(!cycleCols.includes('cycle_seq'), 'autonomy_cycles must not contain retired column cycle_seq');
});
