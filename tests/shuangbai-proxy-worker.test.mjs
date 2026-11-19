import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import vm from 'node:vm';
import {webcrypto,createHmac} from 'node:crypto';

const source = await readFile(new URL('../scripts/shuangbai-proxy-worker.mjs', import.meta.url), 'utf8');
const asset = 'https://shuangbai.online/assets/index-D-3wsGOD.js';

function harness(origin = () => new Response('console.log(1)', {headers: {'content-type': 'application/javascript'}}), brokenCache = false, upstreamOrigin = undefined, secret = undefined) {
  const entries = new Map(), calls = [], pending = [];
  const context = vm.createContext({Request, Response, Headers, URL, Date, TextEncoder, Uint8Array, crypto: webcrypto, caches: {default: {
    async match(key) {if (brokenCache) throw Error('cache unavailable'); return entries.get(key.url)?.clone();},
    async put(key, response) {if (brokenCache) throw Error('cache unavailable'); entries.set(key.url, response.clone());},
  }}, fetch: async (url, init) => {
    calls.push({url, init});
    return url.startsWith('https://cloudflare-dns.com/')
      ? Response.json({Answer: [{data: '"https://example-test.trycloudflare.com"'}]})
      : origin(url, init);
  }});
  vm.runInContext(source.replace('export default {', 'globalThis.worker = {'), context);
  return {entries, calls, async fetch(url = asset, init) {
    const response = await context.worker.fetch(new Request(url, init), {UPSTREAM_ORIGIN: upstreamOrigin, FISHBOT_PROXY_SECRET: secret}, {waitUntil: task => pending.push(task)});
    await Promise.all(pending.splice(0));
    return response;
  }};
}

test('edge signs the actual client, discards forged identity and blocks web access to IPC', async () => {
  const secret='synthetic-edge-secret';
  const h=harness(()=>new Response('ok',{headers:{'content-security-policy':"default-src 'self'"}}),false,'https://origin.example.test',secret);
  const response=await h.fetch('https://shuangbai.online/v1/auth/me',{headers:{'cf-connecting-ip':'203.0.113.1','x-fishbot-client-ip':'attacker','x-fishbot-client-signature':'forged','x-forwarded-for':'attacker'}});
  const headers=h.calls[0].init.headers;
  assert.equal(headers.get('x-fishbot-client-ip'),'203.0.113.1');
  assert.equal(headers.get('x-fishbot-client-signature'),createHmac('sha256',secret).update(headers.get('x-fishbot-client-at')+'\n203.0.113.1').digest('hex'));
  assert.equal(headers.has('x-forwarded-for'),false);
  assert.equal(response.headers.get('content-security-policy'),"default-src 'self'");
  assert.equal((await h.fetch('https://shuangbai.online/internal/v1/bots/test')).status,404);
  assert.equal(h.calls.length,1);
  const unsigned=harness();await unsigned.fetch('https://shuangbai.online/v1/health',{headers:{'x-fishbot-client-ip':'forged','x-fishbot-client-signature':'forged'}});
  assert.equal(unsigned.calls.at(-1).init.headers.has('x-fishbot-client-ip'),false);
});

test('hashed assets are shared at the edge; a hit bypasses DNS and origin, including HEAD', async () => {
  const h = harness();
  const first = await h.fetch(asset, {headers: {cookie: 'fish_session=private', 'if-none-match': 'old'}});
  assert.equal(first.headers.get('x-fishbot-asset-cache'), 'MISS');
  assert.match(first.headers.get('cache-control'), /max-age=31536000.*immutable/);
  assert.equal(h.calls[1].init.headers.has('cookie'), false);
  assert.equal(h.calls[1].init.headers.has('if-none-match'), false);
  assert.equal(await first.text(), 'console.log(1)');
  const second = await h.fetch();
  assert.equal(second.headers.get('x-fishbot-asset-cache'), 'HIT');
  assert.equal(await second.text(), 'console.log(1)');
  assert.equal(h.calls.length, 2);
  const head = await h.fetch(asset, {method: 'HEAD'});
  assert.equal(head.headers.get('x-fishbot-asset-cache'), 'HIT');
  assert.equal(await head.text(), '');
  assert.equal(h.calls.length, 2);
});

test('API authentication, method, body, origin and no-store stay intact', async () => {
  const h = harness(() => Response.json({error: 'unauthorized'}, {status: 401, headers: {'cache-control': 'no-store'}}));
  const r = await h.fetch('https://shuangbai.online/v1/auth/login', {method: 'POST', headers: {'content-type': 'application/json', origin: 'https://shuangbai.online', cookie: 'fish_session=private'}, body: '{}'});
  assert.equal(r.status, 401);
  assert.equal(r.headers.get('cache-control'), 'no-store');
  assert.equal(h.entries.size, 0);
  assert.equal(h.calls[1].init.method, 'POST');
  assert.equal(h.calls[1].init.headers.get('origin'), 'https://shuangbai.online');
  assert.equal(h.calls[1].init.headers.get('cookie'), 'fish_session=private');
  assert.equal(await new Response(h.calls[1].init.body).text(), '{}');
});

test('HTML, query variants, nonhashed files, range and authorization bypass the shared cache', async () => {
  for (const [url, init] of [
    ['https://shuangbai.online/'], ['https://shuangbai.online/assets/index.js'],
    [asset + '?variant=private'], [asset, {headers: {authorization: 'Bearer private'}}],
    [asset, {headers: {range: 'bytes=0-10'}}],
  ]) {
    const h = harness();
    const r = await h.fetch(url, init);
    assert.equal(r.headers.has('x-fishbot-asset-cache'), false, url);
    assert.equal(h.entries.size, 0);
  }
});

test('missing assets returning the SPA HTML shell and errors are never cached', async () => {
  for (const [status, type] of [[200, 'text/html'], [404, 'application/javascript'], [500, 'application/javascript']]) {
    const h = harness(() => new Response('not a public asset', {status, headers: {'content-type': type}}));
    const r = await h.fetch();
    assert.equal(r.status, status);
    assert.equal(r.headers.has('x-fishbot-asset-cache'), false);
    assert.equal(h.entries.size, 0);
  }
});

test('private, cookie-setting and personalized Vary responses are never cached', async () => {
  for (const extra of [{'set-cookie': 'session=private'}, {'cache-control': 'private'}, {'cache-control': 'no-store'}, {'cache-control': 'no-cache'}, {vary: 'Cookie'}, {vary: '*'}]) {
    const h = harness(() => new Response('private', {headers: {'content-type': 'application/javascript', ...extra}}));
    const r = await h.fetch();
    assert.equal(r.headers.has('x-fishbot-asset-cache'), false);
    assert.equal(h.entries.size, 0);
  }
});

test('CSS is cached only with the correct media type', async () => {
  const h = harness(() => new Response('body{}', {headers: {'content-type': 'text/css; charset=utf-8', vary: 'Accept-Encoding'}}));
  const url = 'https://shuangbai.online/assets/index-B9aM6s8J.css';
  assert.equal((await h.fetch(url)).headers.get('x-fishbot-asset-cache'), 'MISS');
  assert.equal((await h.fetch(url)).headers.get('x-fishbot-asset-cache'), 'HIT');
});

test('cache outages fall back to the existing origin path', async () => {
  const h = harness(undefined, true);
  assert.equal(await (await h.fetch()).text(), 'console.log(1)');
  assert.equal(h.calls.length, 2);
});

test('configured named origin skips dynamic DNS and preserves API forwarding', async () => {
  const h=harness(() => Response.json({ok:true}), false, 'https://origin-mobile.shuangbai.online');
  const response=await h.fetch('https://shuangbai.online/v1/health');
  assert.equal(response.status,200);
  assert.equal(h.calls.length,1);
  assert.equal(h.calls[0].url,'https://origin-mobile.shuangbai.online/v1/health');
  assert.equal(response.headers.get('x-upstream-host'),'origin-mobile.shuangbai.online');
  assert.equal(h.entries.size,0);
});

test('proxy preserves the origin security policy and discards untrusted forwarding headers', async () => {
  const policy = "default-src 'self'; frame-ancestors 'none'";
  const h = harness(() => new Response('ok', {headers: {'Content-Security-Policy': policy}}));
  const response = await h.fetch('https://shuangbai.online/', {headers: {'x-forwarded-for': '203.0.113.1', forwarded: 'for=203.0.113.1'}});
  assert.equal(response.headers.get('content-security-policy'), policy);
  assert.equal(h.calls[1].init.headers.has('forwarded'), false);
  assert.equal(h.calls[1].init.headers.has('x-forwarded-for'), false);
});
