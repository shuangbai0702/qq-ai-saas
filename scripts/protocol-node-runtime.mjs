// Preloaded by the Windows protocol launcher before the adapter entry point.
// The launcher places this process in its Job before it starts. We confirm
// that Node has initialized its handles before the launcher transfers the
// Job handle and sends the private startup grant over stdin.
import { randomUUID } from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { StringDecoder } from 'node:string_decoder';
import { setTimeout as delay } from 'node:timers/promises';

const startupTimeoutMs = 12_000;
const configLimit = 64 * 1024;
const logLimit = 2 * 1024 * 1024;
const lineLimit = 65_536;
const logQueueLimit = 32;
const decoder = new TextDecoder('utf-8', { fatal: true, ignoreBOM: true });

async function markReady(hello) {
  if (!hello || typeof hello.root !== 'string' || !path.isAbsolute(hello.root)
    || typeof hello.token !== 'string' || !hello.token) {
    throw new Error('Invalid protocol startup hello');
  }
  const file = path.join(hello.root, '.protocol-launch.json');
  const ticket = JSON.parse(fs.readFileSync(file, 'utf8'));
  if (!ticket || ticket.token !== hello.token || !Number.isFinite(ticket.until)
    || ticket.until <= Date.now() / 1000 || ticket.readyPid !== undefined) {
    throw new Error('Protocol startup ticket did not match');
  }

  // The launcher holds the launch lock throughout this exchange. The ready
  // acknowledgement lives only in its existing, expiring launch ticket.
  const temporary = `${file}.${randomUUID()}`;
  try {
    fs.writeFileSync(temporary, JSON.stringify({ ...ticket, readyPid: process.pid }), {
      flag: 'wx', mode: 0o600,
    });
    const deadline = Date.now() + 200;
    while (true) {
      try {
        fs.renameSync(temporary, file);
        break;
      } catch (error) {
        // Windows can briefly deny replacement while the launcher reads the ticket.
        if (!['EACCES', 'EBUSY', 'EPERM'].includes(error.code) || Date.now() >= deadline) throw error;
        await delay(10);
        const active = JSON.parse(fs.readFileSync(file, 'utf8'));
        if (active?.token !== hello.token || active.until <= Date.now() / 1000) throw error;
      }
    }
  } finally {
    fs.rmSync(temporary, { force: true });
  }
}

async function readLaunchConfig() {
  const input = process.stdin;
  // These are lazy getters. Initialize the streams before the launcher adds
  // the non-inheritable Job handle to this running process.
  void process.stdout;
  void process.stderr;
  let timeout;
  const deadline = new Promise((_, reject) => {
    timeout = setTimeout(() => reject(new Error('Protocol startup timed out')), startupTimeoutMs);
  });
  const received = (async () => {
    let hello;
    let chunks = [];
    let size = 0;
    for await (let chunk of input) {
      if (!hello) {
        const boundary = chunk.indexOf(10);
        const prefix = boundary === -1 ? chunk : chunk.subarray(0, boundary);
        size += prefix.length;
        if (size > configLimit) throw new Error('Protocol startup hello exceeded its limit');
        chunks.push(prefix);
        if (boundary === -1) continue;

        hello = JSON.parse(decoder.decode(Buffer.concat(chunks, size)));
        await markReady(hello);
        chunks = [];
        size = 0;
        chunk = chunk.subarray(boundary + 1);
      }
      size += chunk.length;
      if (size > configLimit) throw new Error('Protocol startup configuration exceeded its limit');
      chunks.push(chunk);
    }
    const config = JSON.parse(decoder.decode(Buffer.concat(chunks, size)));
    if (!hello || config?.root !== hello.root) throw new Error('Protocol startup root changed');
    return config;
  })();
  try {
    return await Promise.race([received, deadline]);
  } finally {
    clearTimeout(timeout);
  }
}

function boundLaunch(config) {
  if (!config || typeof config.root !== 'string' || !path.isAbsolute(config.root)
    || config.pid !== process.pid || !Array.isArray(config.keys)
    || config.keys.some(key => typeof key !== 'string')) return false;

  const authority = config.authority;
  if (authority !== null && (!authority || typeof authority.file !== 'string'
    || !path.isAbsolute(authority.file) || typeof authority.authorityId !== 'string'
    || !authority.authorityId)) return false;

  const file = path.join(config.root, 'protocol.process.json');
  const record = JSON.parse(fs.readFileSync(file, 'utf8'));
  return record?.kind === 'protocol' && record.pid === config.pid
    && Number.isFinite(record.created) && record.created > 0
    && (config.created === undefined || config.created === record.created);
}

function authorityMatches(authority) {
  if (authority === null) return true;
  try {
    return decoder.decode(fs.readFileSync(authority.file)).trim() === authority.authorityId;
  } catch {
    return false;
  }
}

function redact(line, keys) {
  for (const key of keys) line = line.replaceAll(key, '[REDACTED]');
  return line.replace(/fish-platform\.[0-9a-f-]{36}\.[A-Za-z0-9_-]{43}/gi, '[REDACTED]')
    .replace(/Bearer\s+[\w.\-]+/gi, 'Bearer [REDACTED]')
    .replace(/sk-[A-Za-z0-9_-]{8,}/g, '[REDACTED]');
}

function protocolLog(root, secrets) {
  const keys = secrets.filter(key => key.length > 2);
  const file = path.join(root, 'runtime.log');
  const queue = [];
  let scheduled;

  function append(text) {
    try {
      if ((fs.statSync(file, { throwIfNoEntry: false })?.size ?? 0) > logLimit) {
        fs.renameSync(file, file + '.previous');
      }
      fs.appendFileSync(file, text, { mode: 0o600 });
    } catch {
      // Rotation can race the Agent or a log reader. Logging is best effort.
    }
  }

  function flush() {
    if (scheduled) clearImmediate(scheduled);
    scheduled = undefined;
    if (!queue.length) return;
    append(queue.splice(0).join(''));
  }

  return {
    line(text) {
      if (queue.length >= logQueueLimit) return;
      queue.push(`[protocol] ${redact(text, keys)}\n`);
      scheduled ??= setImmediate(flush).unref();
    },
    audit(text) {
      flush();
      append(`[protocol] ${text}\n`);
    },
  };
}

function attachLog(stream, log) {
  const textDecoder = new StringDecoder('utf8');
  const original = stream.write;
  let line = '';
  let discard = false;

  function collect(text) {
    const parts = text.split('\n');
    for (let index = 0; index < parts.length; index++) {
      const part = parts[index];
      if (!discard && line.length + part.length > lineLimit) {
        line = '';
        discard = true;
      }
      if (!discard) line += part;
      if (index === parts.length - 1) continue;
      if (!discard) log.line(line);
      line = '';
      discard = false;
    }
  }

  stream.write = function (chunk, encoding, callback) {
    if (typeof encoding === 'function') {
      callback = encoding;
      encoding = undefined;
    }
    const bytes = typeof chunk === 'string' ? Buffer.from(chunk, encoding) : chunk;
    if (!ArrayBuffer.isView(bytes)) return original.call(this, chunk, encoding, callback);
    const buffer = Buffer.from(bytes.buffer, bytes.byteOffset, bytes.byteLength);
    for (let offset = 0; offset < buffer.length; offset += 4096) {
      collect(textDecoder.write(buffer.subarray(offset, offset + 4096)));
    }
    if (typeof callback === 'function') process.nextTick(callback);
    return true;
  };

  return () => {
    collect(textDecoder.end());
    if (!discard && line) log.line(line);
  };
}

async function boot() {
  const config = await readLaunchConfig();
  if (!boundLaunch(config)) throw new Error('Protocol launch record did not match');

  const { authority, root, keys } = config;
  const log = protocolLog(root, keys);
  function revoke() {
    log.audit('execution authority revoked');
    process.exit(1);
  }
  if (!authorityMatches(authority)) revoke();

  const flushStdout = attachLog(process.stdout, log);
  const flushStderr = attachLog(process.stderr, log);
  log.audit(`process started at=${new Date().toISOString()} pid=${process.pid} source=ProcessHost.start`);

  const authorityCheck = authority === null ? undefined : setInterval(() => {
    if (!authorityMatches(authority)) revoke();
  }, 500).unref();
  process.once('exit', code => {
    clearInterval(authorityCheck);
    flushStdout();
    flushStderr();
    log.audit(`process exited (${code}) at=${new Date().toISOString()} pid=${process.pid} intent=unexpected-or-external`);
    // The launch helper verifies and replaces a stale ownership record.
  });
}

try {
  await boot();
} catch {
  // An invalid startup payload can contain credentials or untrusted paths.
  process.exit(1);
}
