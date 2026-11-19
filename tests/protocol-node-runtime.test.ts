import test, { type TestContext } from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { mkdir, mkdtemp, readFile, rename, rm, writeFile } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';

const bootstrap = new URL('../scripts/protocol-node-runtime.mjs', import.meta.url).href;
const secret = 'synthetic-private-key';
const created = 10;

async function startFixture(t: TestContext, root: string, script: string, options: {
  authority?: { file: string; authorityId: string };
  mismatchedRecord?: boolean;
} = {}) {
  const child = spawn(process.execPath, ['--import', bootstrap, '--eval', script], {
    windowsHide: true,
    stdio: ['pipe', 'pipe', 'pipe', 'ipc'],
  });
  const { stdin, stdout, stderr } = child;
  assert.ok(stdin && stdout && stderr);
  const output: Buffer[] = [];
  stdout.on('data', chunk => output.push(chunk));
  stderr.on('data', chunk => output.push(chunk));
  stdin.on('error', () => {});
  const done = once(child, 'close');
  t.after(async () => {
    child.kill();
    await done;
  });
  const ready = () => once(child, 'message').then(([message]) => message);
  const token = 'fixture-launch-nonce';
  const ticket = path.join(root, '.protocol-launch.json');
  const deadline = Date.now() + 5_000;
  await writeFile(ticket, JSON.stringify({ token, until: deadline / 1000, config: 'encrypted-fixture' }));
  stdin.write(JSON.stringify({ root, token }) + '\n');
  return {
    async activate() {
      while (JSON.parse(await readFile(ticket, 'utf8')).readyPid !== child.pid) {
        assert.ok(child.exitCode === null && Date.now() < deadline, 'protocol did not acknowledge its ticket');
        await delay(20);
      }
      await writeFile(path.join(root, 'protocol.process.json'), JSON.stringify({
        kind: 'protocol', pid: child.pid, created: options.mismatchedRecord ? created + 1 : created,
      }));
      stdin.end(JSON.stringify({
        root, pid: child.pid, created, authority: options.authority ?? null, keys: [secret],
      }));
    },
    done,
    output: () => Buffer.concat(output).toString('utf8'),
    ready,
  };
}

async function privateRoot(t: TestContext) {
  const root = await mkdtemp(path.join(os.tmpdir(), 'fishbot protocol runtime '));
  t.after(() => rm(root, { recursive: true, force: true }));
  return root;
}

test('protocol preload rotates logs and redacts complete lines from both streams', { timeout: 8_000 }, async t => {
  const root = await privateRoot(t);
  const logFile = path.join(root, 'runtime.log');
  const old = 'x'.repeat(2 * 1024 * 1024 + 1);
  await writeFile(logFile, old);
  const fixture = await startFixture(t, root, `
    const utf8 = Buffer.from('🦀');
    process.stdout.write(utf8.subarray(0, 2));
    process.stdout.write(utf8.subarray(2));
    process.stdout.write(' synthetic-', () => console.log('first callback'));
    process.stdout.write(Buffer.from('private-key\\n').toString('hex'), 'hex', () => console.log('second callback'));
    process.stdout.write('a'.repeat(66_000));
    process.stdout.write(' synthetic-private-key oversized footer\\n');
    process.stderr.write('Bearer credential-from-a-header\\n');
    process.stderr.write('sk-abcdefghij');
  `);
  await fixture.activate();
  assert.deepEqual(await fixture.done, [0, null]);
  const log = await readFile(logFile, 'utf8');
  assert.equal(fixture.output(), '');
  assert.equal((await readFile(logFile + '.previous', 'utf8')).length, old.length);
  assert.match(log, /\[protocol\] process started/);
  assert.match(log, /\[protocol\] 🦀 \[REDACTED\]/);
  assert.match(log, /\[protocol\] first callback\n\[protocol\] second callback/);
  assert.match(log, /\[protocol\] Bearer \[REDACTED\]/);
  assert.match(log, /\[protocol\] \[REDACTED\]\n\[protocol\] process exited \(0\)/);
  assert.doesNotMatch(log, /synthetic-private-key|credential-from-a-header|sk-abcdefghij|oversized footer/);
});

test('protocol preload refuses changed ownership or missing authority before executing the entry point', { timeout: 8_000 }, async t => {
  const root = await privateRoot(t);
  const logFile = path.join(root, 'runtime.log');
  const script = "process.stdout.write('entry point executed'); process.exit(92)";
  const moved = await startFixture(t, root, script, { mismatchedRecord: true });
  await moved.activate();
  assert.deepEqual(await moved.done, [1, null]);
  assert.equal(moved.output(), '');
  await assert.rejects(readFile(logFile), { code: 'ENOENT' });

  const revoked = await startFixture(t, root, script, {
    authority: { file: path.join(root, 'authority'), authorityId: 'fixture' },
  });
  await revoked.activate();
  assert.deepEqual(await revoked.done, [1, null]);
  const log = await readFile(logFile, 'utf8');
  assert.match(log, /execution authority revoked/);
  assert.doesNotMatch(log, /entry point executed|process started/);
});

test('running protocol exits when the authority directory moves', { timeout: 8_000 }, async t => {
  const root = await privateRoot(t);
  const directory = path.join(root, 'authority-dir');
  const file = path.join(directory, 'authority');
  await mkdir(directory);
  await writeFile(file, 'fixture');
  const fixture = await startFixture(t, root, "process.send('ready'); setInterval(() => {}, 1000)", {
    authority: { file, authorityId: 'fixture' },
  });
  const ready = fixture.ready();
  await fixture.activate();
  assert.equal(await Promise.race([ready, fixture.done]), 'ready');
  await rename(directory, path.join(root, 'revoked-dir'));
  assert.deepEqual(await fixture.done, [1, null]);
  const log = await readFile(path.join(root, 'runtime.log'), 'utf8');
  assert.match(log, /execution authority revoked\n\[protocol\] process exited \(1\)/);
});
