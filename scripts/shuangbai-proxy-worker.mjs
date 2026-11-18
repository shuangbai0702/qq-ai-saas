
// ============================================================
//  shuangbai 反向代理 Worker
//  上游地址不写死：从 DuckDNS 的 TXT 记录动态读取（DoH）
//  服务器那边 cf-txt-sync.timer 每分钟把当前隧道地址写进去
// ============================================================

const DOH_URL = 'https://cloudflare-dns.com/dns-query?name=shuangbai.duckdns.org&type=TXT';
const CACHE_MS = 30000;

let cached = { url: null, at: 0 };

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

function parseTxt(json) {
  const answers = (json && json.Answer) || [];
  const joined = answers.map((a) => a.data).join(' ');
  const m = joined.match(/https:\/\/[a-z0-9-]+\.trycloudflare\.com/i);
  return m ? m[0] : null;
}

async function resolveUpstream() {
  const now = Date.now();
  if (cached.url && now - cached.at < CACHE_MS) return cached.url;
  try {
    const r = await fetch(DOH_URL, {
      headers: { accept: 'application/dns-json' },
      cf: { cacheTtl: 15, cacheEverything: true },
    });
    const j = await r.json();
    const url = parseTxt(j);
    if (url) {
      cached = { url, at: now };
      return url;
    }
  } catch (e) {
    /* fall through to stale cache */
  }
  return cached.url;
}

export default {
  async fetch(request, env, ctx) {
    const key = assetCacheKey(request);
    const edgeCache = key ? caches.default : null;
    if (edgeCache) {
      try {
        const hit = await edgeCache.match(key);
        if (hit) {
          const headers = new Headers(hit.headers);
          headers.set('x-fishbot-asset-cache', 'HIT');
          return new Response(request.method === 'HEAD' ? null : hit.body, { status: hit.status, headers });
        }
      } catch { /* Cache failures must not make the website unavailable. */ }
    }
    // A named tunnel has a stable origin and needs no DuckDNS lookup or URL synchronization.
    const origin = env.UPSTREAM_ORIGIN || await resolveUpstream();
    if (!origin) {
      return new Response('上游地址暂时取不到（DuckDNS TXT 未同步）', { status: 503, headers: { 'content-type': 'text/plain; charset=utf-8' } });
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
    headers.set('x-forwarded-proto', 'https');

    const init = { method: request.method, headers, redirect: 'manual' };
    if (!['GET', 'HEAD'].includes(request.method)) init.body = request.body;

    let resp;
    try {
      resp = await fetch(target, init);
    } catch (e) {
      return new Response('服务暂时无法连接', { status: 502, headers: { 'content-type': 'text/plain; charset=utf-8', 'cache-control': 'no-store' } });
    }

    const out = new Headers(resp.headers);
    out.delete('content-encoding');
    out.delete('content-length');
    out.delete('x-robots-tag');
    out.set('x-upstream-host', new URL(origin).host);
    if (key && cacheableAsset(resp, u.pathname)) {
      out.set('cache-control', 'public, max-age=31536000, s-maxage=604800, immutable');
      out.set('x-fishbot-asset-cache', 'MISS');
      const response = new Response(resp.body, { status: resp.status, statusText: resp.statusText, headers: out });
      if (request.method === 'GET') ctx.waitUntil(edgeCache.put(key, response.clone()).catch(() => {}));
      return response;
    }
    return new Response(resp.body, { status: resp.status, statusText: resp.statusText, headers: out });
  },
};
