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
    else if (entry.name.endsWith('.ts') || entry.name.endsWith('.tsx') || entry.name.endsWith('.mjs')) out.push(path.resolve(full));
  }
  return out;
}

const IMPORT_RE = /(?:import|export)\s+(?:[^'"]*?\s+from\s+)?['"]([^'"]+)['"]|(?:import|require)\s*\(\s*['"]([^'"]+)['"]\s*\)/g;

function resolveSpec(src: string, spec: string, known: Set<string>): string | null {
  if (spec.endsWith('.css') || spec.endsWith('.json') || spec.endsWith('.svg') || spec.endsWith('.png')) return null;
  if (spec.startsWith('.')) {
    const base = path.resolve(path.dirname(src), spec);
    const candidates = [base, `${base}.ts`, `${base}.tsx`, `${base}.mjs`, path.join(base, 'index.ts'), path.join(base, 'index.tsx')];
    if (spec.endsWith('.js')) {
      const stem = path.resolve(path.dirname(src), spec.slice(0, -3));
      candidates.unshift(`${stem}.ts`, `${stem}.tsx`, `${stem}.mjs`);
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
      const spec = match[1] || match[2];
      const target = resolveSpec(file, spec, known);
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
      if (rel.startsWith('apps/control-api/') && targetRel.startsWith('apps/node-agent/') && rel !== 'apps/control-api/src/app.ts') {
        violations.push(`control-api->node-agent penetration: ${rel} -> ${targetRel}`);
      }
      if (rel.startsWith('apps/control-api/') && targetRel.startsWith('adapters/')) {
        violations.push(`control-api->adapter penetration: ${rel} -> ${targetRel}`);
      }
      if (rel.startsWith('apps/node-agent/') && targetRel.startsWith('apps/control-api/')) {
        violations.push(`node-agent->control-api inversion: ${rel} -> ${targetRel}`);
      }
      if (rel.startsWith('apps/web/') && (targetRel.startsWith('apps/control-api/') || targetRel.startsWith('apps/node-agent/') || targetRel.startsWith('adapters/') || targetRel.startsWith('packages/database/'))) {
        violations.push(`web->backend penetration: ${rel} -> ${targetRel}`);
      }
    }
    if ((rel.startsWith('adapters/protocol/') || rel.startsWith('adapters/mock/')) && /\b(apiKey|AiRuntimeContext)\b/.test(text)) {
      violations.push(`protocol adapter references AI secret/context: ${rel}`);
    }
    if (rel === 'apps/node-agent/src/dsh-host.ts' && /\.(query|transaction)\(/.test(text)) {
      violations.push(`dsh-host executes raw SQL instead of packages/database/dsh.ts: ${rel}`);
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

  // Verify externalAgent logSecrets resolves platform model key for log redaction
  const { createExternalRuntimeControl } = await import('../apps/control-api/src/bot-runtime-reader.js');
  const { loadKey } = await import('../packages/shared/security.js');
  const key = await loadKey(root, { createIfMissing: false });
  await db.query('UPDATE bot_config_revisions SET config=$2 WHERE bot_id=$1 AND revision=1', [botId, JSON.stringify({ ...defaultConfig, platformModel: true })]);
  const [botRow] = await db.query('SELECT * FROM bots WHERE id=$1', [botId]);
  const secrets = await createExternalRuntimeControl(db, root, key).logSecrets(botRow);
  assert.match(secrets.apiKey, /^fish-platform\./, 'externalAgent logSecrets must resolve platform model key for redaction');
});

test('ProtocolContext isolates QQ protocol adapters from LLM apiKey and BotConfig', async t => {
  const root = await mkdtemp(path.join(os.tmpdir(), 'fishbot-pctx-'));
  const { app, db, agent } = await createApp({ root, agent: false });
  t.after(() => app.close());
  const reg = await app.inject({
    method: 'POST',
    url: '/v1/auth/register',
    payload: { inviteCode: await issueTestInvite(db), email: 'pctx@example.com', password: 'test-password-123', name: 'pctx' },
  });
  const cookie = reg.cookies[0].value;
  const created = await app.inject({
    method: 'POST',
    url: '/v1/bots',
    cookies: { fish_session: cookie },
    payload: { name: 'ProtoIsolationBot', protocol: 'mock', apiKey: 'sk-super-secret-llm-key', config: defaultConfig },
  });
  assert.equal(created.statusCode, 201, created.body);
  const botId = created.json().id;
  await db.query('UPDATE bot_config_revisions SET config=$2 WHERE bot_id=$1 AND revision=1', [botId, JSON.stringify({ ...defaultConfig, model: 'deepseek-chat' })]);
  const [botRow] = await db.query('SELECT * FROM bots WHERE id=$1', [botId]);
  const pctx = await agent.protocolContext(botRow);
  assert.equal('apiKey' in pctx, false, 'ProtocolContext must never carry LLM apiKey');
  assert.equal('config' in pctx, false, 'ProtocolContext must never carry BotConfig');
  assert.equal('personaEdit' in pctx, false, 'ProtocolContext must never carry AI edit permits');
  assert.equal(pctx.botId, botRow.id);
  const aictx = await agent.context(botRow);
  assert.equal(aictx.apiKey, 'sk-super-secret-llm-key');
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

test('Bilibili configure blocks two-step model baseUrl exfiltration across provider switches', async t => {
  const root = await mkdtemp(path.join(os.tmpdir(), 'fishbot-bili-exfil-'));
  const { app, db } = await createApp({ root, agent: false });
  t.after(() => app.close());

  const reg = await app.inject({
    method: 'POST',
    url: '/v1/auth/register',
    payload: { inviteCode: await issueTestInvite(db), email: 'bili-sec@example.com', password: 'test-password-123', name: 'sec' },
  });
  const cookie = reg.cookies[0].value;
  const created = await app.inject({
    method: 'POST',
    url: '/v1/bots',
    cookies: { fish_session: cookie },
    payload: { name: 'BiliSecBot', protocol: 'mock', config: defaultConfig },
  });
  assert.equal(created.statusCode, 201, created.body);
  const botId = created.json().id;

  const initial = (await app.inject({ method: 'GET', url: `/v1/bots/${botId}/bilibili`, cookies: { fish_session: cookie } })).json();
  const step1 = await app.inject({
    method: 'PUT',
    url: `/v1/bots/${botId}/bilibili`,
    cookies: { fish_session: cookie },
    payload: {
      expectedRevision: initial.revision,
      config: { ...initial.config, model: { provider: 'openai', baseUrl: 'https://legit.example/v1', model: 'gpt-4o-mini' } },
      modelKey: 'sk-legit-bili-key',
    },
  });
  assert.equal(step1.statusCode, 200, step1.body);
  const rev2 = step1.json().revision;

  // Attempt step 1 of two-step exfiltration: switch provider='none' while changing baseUrl without clearing/replacing key
  const exfilAttempt = await app.inject({
    method: 'PUT',
    url: `/v1/bots/${botId}/bilibili`,
    cookies: { fish_session: cookie },
    payload: {
      expectedRevision: rev2,
      config: { ...initial.config, model: { provider: 'none', baseUrl: 'https://attacker.example/v1', model: 'gpt-4o-mini' } },
    },
  });
  assert.equal(exfilAttempt.statusCode, 400, 'Changing baseUrl while retaining saved model_secret must be blocked regardless of provider');

  // Clearing key explicitly allows changing baseUrl safely
  const cleared = await app.inject({
    method: 'PUT',
    url: `/v1/bots/${botId}/bilibili`,
    cookies: { fish_session: cookie },
    payload: {
      expectedRevision: rev2,
      config: { ...initial.config, model: { provider: 'none', baseUrl: 'https://other.example/v1', model: 'gpt-4o-mini' } },
      clearModelKey: true,
    },
  });
  assert.equal(cleared.statusCode, 200, cleared.body);
});

test('Platform model has_key/hasKey reflects real available credits and personal fallback across API endpoints', async t => {
  const root = await mkdtemp(path.join(os.tmpdir(), 'fishbot-avail-'));
  const { app, db } = await createApp({ root, agent: false });
  t.after(() => app.close());

  const reg = await app.inject({
    method: 'POST',
    url: '/v1/auth/register',
    payload: { inviteCode: await issueTestInvite(db), email: 'avail@example.com', password: 'test-password-123', name: 'avail' },
  });
  const cookie = reg.cookies[0].value;
  const [{ tenant_id: tenantId }] = await db.query('SELECT tenant_id FROM memberships WHERE user_id=$1', [reg.json().user.id]);

  // Enable a platform provider and grant expiring campaign credit
  await db.query("INSERT INTO platform_model_providers(id,name,base_url,model,secret,priority,enabled) VALUES('p-test','P','https://x.invalid/v1','m','cipher',1,true)");
  await db.query("INSERT INTO platform_model_credits(tenant_id,granted,campaign_granted,campaign_expires_at) VALUES($1,5000,5000,now()+interval '1 hour')", [tenantId]);

  const created = await app.inject({
    method: 'POST',
    url: '/v1/bots',
    cookies: { fish_session: cookie },
    payload: { name: 'PlatformBot', protocol: 'mock', config: { ...defaultConfig, platformModel: true, model: '' } },
  });
  assert.equal(created.statusCode, 201, created.body);
  const botId = created.json().id;

  let list = (await app.inject({ method: 'GET', url: '/v1/bots', cookies: { fish_session: cookie } })).json();
  let detail = (await app.inject({ method: 'GET', url: `/v1/bots/${botId}`, cookies: { fish_session: cookie } })).json();
  assert.equal(list.items[0].has_key, true);
  assert.equal(detail.hasKey, true);

  // Expire campaign credit -> both list and detail must report false when no personal fallback exists
  await db.query("UPDATE platform_model_credits SET campaign_expires_at=now()-interval '1 hour' WHERE tenant_id=$1", [tenantId]);
  list = (await app.inject({ method: 'GET', url: '/v1/bots', cookies: { fish_session: cookie } })).json();
  detail = (await app.inject({ method: 'GET', url: `/v1/bots/${botId}`, cookies: { fish_session: cookie } })).json();
  assert.equal(list.items[0].has_key, false);
  assert.equal(detail.hasKey, false);

  // Creating a new platformModel bot with expired (0 available) credits must be rejected with 403
  const blocked = await app.inject({
    method: 'POST',
    url: '/v1/bots',
    cookies: { fish_session: cookie },
    payload: { name: 'BlockedPlatformBot', protocol: 'mock', config: { ...defaultConfig, platformModel: true } },
  });
  assert.equal(blocked.statusCode, 403, 'Creating a new platformModel bot when available credits=0 must be rejected');
});
