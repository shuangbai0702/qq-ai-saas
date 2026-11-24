// Client identity for outbound model traffic.
//
// Codex-oriented relays (for example xingchiapi.com) allow-list programming
// clients by `User-Agent` and answer 403 client_not_allowed for anything they
// do not recognise. DeepSeek Harness sends the RFC 9110 product+comment form
// below on every provider request, so model traffic from this service presents
// the same identity instead of a bare HTTP default. The version is
// operator-overridable so it can track an installed harness without a code
// change; an unusable override falls back to the pinned value rather than
// emitting a header the relay cannot parse.
export const DSH_HARNESS_URL = 'https://github.com/deepseek-ai/deepseek-harness';
const PINNED_VERSION = '0.2.0-rc.2';
export function dshHarnessVersion() {
  const value = (process.env.FISHBOT_DSH_HARNESS_VERSION || '').trim();
  return value.length <= 64 && /^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?$/.test(value) ? value : PINNED_VERSION;
}
export function dshClientHeaders(): Record<string, string> {
  return { 'user-agent': `deepseek-harness/${dshHarnessVersion()} (+${DSH_HARNESS_URL})` };
}
