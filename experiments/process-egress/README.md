# Process egress proof of concept

This module contains two independent pieces for a future Windows process
egress service:

- An isolated userspace WireGuard stack per peer. Its TCP, UDP and DNS
  methods use the configured peer. The module neither creates host routes
  nor changes the system DNS resolver.
- A Windows process identity check that pins an open process handle,
  checks its exact FILETIME creation timestamp and image, and requires
  membership in the trusted launcher's Job Object.

The Windows helper checks identity only. It does not enforce a network
policy, admit a NapCat process, or install a driver. The test fixture uses
real Node processes and loopback WireGuard peers. It does not qualify a
public gateway, the Windows DNS Client, or a QQ session.

## Verify locally

```text
go test -count=1 -timeout=30s ./...
go vet ./...
go build ./...
```

The Windows identity test uses `node.exe` on PATH. WireGuard fixture keys
are generated per test. Separate gateway stacks respond with distinct
labels so two paths to the same numeric target cannot be confused.

`wgprobe` is a one-shot operator tool for a provisioned gateway. Build it
with `go build ./cmd/wgprobe`. Pass the protected tunnel configuration
JSON on stdin. Fields are `addresses`, `resolvers`, `privateKey`, `peerKey`,
and `endpoint`. The keys use WireGuard UAPI hexadecimal format. The
endpoint and the `-target` probe address must be numeric. Configure
`-server-name` with the TLS host and `-expect-ip` with the expected source
address. The probe uses HTTPS `GET /` on a controlled responder that
returns only the observed source IP. Optional `-dns` resolves a name
inside the tunnel. The command opens no management listener.

The transport and probe can be tested without driver installation.
FishBot admission requires an independently verified signed WFP
implementation and Windows resolver containment. This module is not
registered with production startup.
