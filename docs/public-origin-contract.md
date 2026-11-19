# Public origin ingress contract

The public website has one authenticated ingress: the Worker forwards a fresh
client identity signed with the existing proxy secret. Public Tunnel rules must
overwrite the origin HTTP Host with `fishbot-edge-ingress.invalid`. The control
API validates the signature for that Host before authentication or database work;
runtime IPC routes on that ingress return 404 even with a valid edge signature.

This deployment requires the API listener to bind to loopback only. Every public
Tunnel hostname, including aliases, must use the forced Host. Ordinary local
runtime calls keep their localhost Host and independent bearer verification.
Do not exempt an unsigned request merely because the tunnel connects from a
loopback address, and do not trust client-supplied Forwarded headers.

Deployment verification must prove all of the following:

- the actual listener binds only to loopback;
- the live Tunnel configuration overwrites Host for every public ingress rule;
- unsigned direct-origin requests are rejected;
- the Worker path reaches login/health normally with a signed identity;
- encoded IPC paths are rejected without entering the runtime handler;
- local health checks and runtime bearer authentication retain their behavior.

The Worker requires a configured named origin and signing secret. There is no
public-DNS fallback. HSTS is host-only until HTTPS support for all subdomains has
been verified. Public health retains `ok` and `publicOrigin` because the latter
is used by the existing website redirect and tunnel health checks; detailed
runtime diagnostics belong to authenticated administration APIs.
