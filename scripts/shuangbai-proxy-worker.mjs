// Public website proxy. The named origin and signing secret are required.
async function signClient(headers, request, secret) {
  const ip = request.headers.get('cf-connecting-ip');
  if (!secret || !ip) return;
  const at = String(Date.now());
  const encoder = new TextEncoder();
  const key = await crypto.subtle.importKey('raw', encoder.encode(secret), {name: 'HMAC', hash: 'SHA-256'}, false, ['sign']);
  const signed = await crypto.subtle.sign('HMAC', key, encoder.encode(at + '\n' + ip));
  headers.set('x-fishbot-client-ip', ip);
  headers.set('x-fishbot-client-at', at);
  headers.set('x-fishbot-client-signature', Array.from(new Uint8Array(signed), b => b.toString(16).padStart(2, '0')).join(''));
}

// Only Vite's public, content-hashed JS/CSS may be shared across visitors.
// HTML, API responses, query variants and authenticated requests stay on origin.
function assetCacheKey(request) {
  const url = new URL(request.url);
  if (!['GET', 'HEAD'].includes(request.method) || url.search ||
      request.headers.has('authorization') || request.headers.has('range') ||
      !/^\/assets\/[A-Za-z0-9_-]+-[A-Za-z0-9_-]{8,}\.(?:js|css)$/.test(url.pathname)) return null;
  return new Request(url.href, { method: 'GET' });
}

function cacheableAsset(response, pathname) {
  const type = (response.headers.get('content-type') || '').split(';')[0].trim().toLowerCase();
  const control = response.headers.get('cache-control') || '';
  const vary = response.headers.get('vary') || '';
  return response.status === 200 && !response.headers.has('set-cookie') &&
    !/\b(?:private|no-store|no-cache)\b/i.test(control) &&
    vary.split(',').every(value => !value.trim() || value.trim().toLowerCase() === 'accept-encoding') &&
    (pathname.endsWith('.css') ? type === 'text/css' : ['application/javascript', 'text/javascript'].includes(type));
}

function secureResponse(response) {
  const headers = new Headers(response.headers);
  headers.set('strict-transport-security', 'max-age=31536000');
  headers.delete('x-upstream-host');
  headers.delete('x-fishbot-static');
  return new Response(response.body, {status: response.status, statusText: response.statusText, headers});
}

function publicPathname(url) {
  let pathname = new URL(url).pathname;
  for (let depth = 0; depth < 4 && /%[a-f0-9]{2}/i.test(pathname); depth++) pathname = decodeURIComponent(pathname);
  if (/%[a-f0-9]{2}/i.test(pathname)) throw new Error('Excessive path encoding');
  pathname = pathname.replace(/\\/g, '/').replace(/\/+/g, '/');
  return new URL('https://edge.invalid' + pathname).pathname;
}

export default {
  async fetch(request, env, ctx) {
    // Runtime IPC has its own bearer authentication and is never a web API.
    let pathname;
    try { pathname = publicPathname(request.url); }
    catch { return secureResponse(new Response('Invalid path', {status: 400, headers: {'cache-control': 'no-store'}})); }
    if (/^\/internal(?:[\/;]|$)/i.test(pathname)) {
      return secureResponse(new Response('Not found', {status: 404, headers: {'cache-control': 'no-store'}}));
    }
    const origin = env.UPSTREAM_ORIGIN;
    if (!origin || !env.FISHBOT_PROXY_SECRET) {
      return secureResponse(new Response('服务暂时不可用', {status: 503, headers: {'cache-control': 'no-store'}}));
    }
    const key = assetCacheKey(request);
    const edgeCache = key ? caches.default : null;
    if (edgeCache) {
      try {
        const hit = await edgeCache.match(key);
        if (hit) {
          const headers = new Headers(hit.headers);
          headers.set('x-fishbot-asset-cache', 'HIT');
          return secureResponse(new Response(request.method === 'HEAD' ? null : hit.body, { status: hit.status, headers }));
        }
      } catch { /* Cache failures must not make the website unavailable. */ }
    }
    const u = new URL(request.url);
    const target = origin.replace(/\/+$/, '') + u.pathname + u.search;

    const headers = new Headers(request.headers);
    if (key) {
      // These files are public. Never let a session customize a shared response.
      headers.delete('cookie');
      headers.delete('if-none-match');
      headers.delete('if-modified-since');
    }
    ['host', 'cf-connecting-ip', 'cf-ipcountry', 'cf-ray', 'cf-visitor', 'forwarded', 'x-forwarded-for', 'x-forwarded-host'].forEach((h) => headers.delete(h));
    ['x-fishbot-client-ip', 'x-fishbot-client-at', 'x-fishbot-client-signature'].forEach(h => headers.delete(h));
    await signClient(headers, request, env.FISHBOT_PROXY_SECRET);
    headers.set('x-forwarded-proto', 'https');

    const init = { method: request.method, headers, redirect: 'manual' };
    if (!['GET', 'HEAD'].includes(request.method)) init.body = request.body;

    let resp;
    try {
      resp = await fetch(target, init);
    } catch (e) {
      return secureResponse(new Response('服务暂时无法连接', { status: 502, headers: { 'content-type': 'text/plain; charset=utf-8', 'cache-control': 'no-store' } }));
    }

    const out = new Headers(resp.headers);
    out.delete('content-encoding');
    out.delete('content-length');
    out.delete('x-robots-tag');
    out.delete('x-upstream-host');
    if (key && cacheableAsset(resp, u.pathname)) {
      out.set('cache-control', 'public, max-age=31536000, s-maxage=604800, immutable');
      out.set('x-fishbot-asset-cache', 'MISS');
      const response = new Response(resp.body, { status: resp.status, statusText: resp.statusText, headers: out });
      if (request.method === 'GET') ctx.waitUntil(edgeCache.put(key, response.clone()).catch(() => {}));
      return secureResponse(response);
    }
    return secureResponse(new Response(resp.body, { status: resp.status, statusText: resp.statusText, headers: out }));
  },
};
