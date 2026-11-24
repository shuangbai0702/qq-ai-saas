# LLBot protocol backend

LLBot is a second `ProtocolAdapter`, not an AI runtime. Existing Bots stay on
NapCat. Create with `protocol: llbot`, or stop the Bot and use the QQ page's
protocol selector. Switching is a durable, tenant-owned command fenced by
revision/generation. It retains the Bot ID, QQ binding, config revisions and
Journal. It does not translate QQ client sessions; login to the same QQ again.

## Local installation

This baseline is the reviewed **8.3.2 test Windows x64 archive**. Install without
running its launchers:

```powershell
npm run setup:llbot -- --archive "path/to/91拉拉逼8.3.2测试.zip"
$env:LLBOT_RUNTIME_DIR = "absolute/path/to/references/llbot-8.3.2"
$env:LLBOT_QQ_PATH = "absolute/path/to/a/matching/QQ.exe"
# Optional: set LLBOT_AUTH_TOKEN only when using official token authentication.
```

The setup command requires a new destination. It checks the complete ZIP and
executed JS hashes, applies exact single-occurrence patches, bundles our action
extensions, checks JS syntax and records runtime hashes. An unfamiliar version
fails installation. It never executes the bundled Node/PMHQ/native modules.
Original launchers, hosts edits and global process-scanning scripts are excluded.
The reviewed package's local authentication material is installed separately.
Without LLBOT_AUTH_TOKEN, each managed PMHQ uses our private CONNECT endpoint and
native helper. The helper verifies its exact parent, PID, birth time and executable
and holds that process handle; it never enumerates or opens another Bot's PMHQ.
No hosts edits or port 443 are needed. The proxy environment is set only for PMHQ
and removed from its QQ child; it is independent of the QQ network egress policy.
With LLBOT_AUTH_TOKEN configured, official authentication remains available.
The PMHQ mode is explicitly inline, avoiding its default proxy mode's global QQ
proxy-file changes. A matching Linux native distribution has not been integrated.

## Ownership and transport

`index.ts` owns per-Bot settings/manifest and delegates start/stop/inspection to
ProcessHost. `runner.mjs` owns the management HTTP/WS relay, PMHQ and the LLBot
child. Each Bot has private QQ/user-data/config paths and its existing four-port
allocation: AI, PMHQ, management, private LLBot WebUI. Management and reverse WS
use unique per-Bot tokens. The QQ bootstrap proves the native private profile
hook and applies that QQ session's native MSF proxy before the relay admits it.
It does not set a machine/browser/global proxy. If the existing optional
`NAPCAT_EGRESS_PROXY_PORT` is configured, both adapters use the same pure random
assignment / exponential fallback policy, with independent Bot settings.

`actions.mjs` adds signature, verified favorite detail/upload and Qzone publishing
at the actual upstream action registry. Existing actions remain upstream-owned.
QQ cookies stay here and are sent only to fixed QQ origins; the AI gets ordinary
OneBot receipts. Writes are not automatically replayed after timeouts/disconnects.
Favorite metadata never invents content MD5; first reads may download up to 200
images (4 concurrently, 8 MiB each, 10 seconds each). Partial catalogs fail rather
than retiring valid items. Subsequent reads reuse verified bytes' hashes during
that LLBot process lifetime. The read deadline is deliberately 10 minutes, unlike
ordinary write/query deadlines. No new model calls or context fields are added.

## Resource policy

LLBot JS has a 640 MiB heap ceiling and reports heap/RSS over parent IPC. After
120 seconds of business inactivity, pressure first requests JS GC. Sustained
pressure can recycle **only LLBot**, at most 3 recoveries/hour. PMHQ/QQ remain in
the owned protocol tree. PMHQ exit or sustained HTTP unresponsiveness is reported
for the existing Agent recovery command, with its original ownership/rate limits.
Tree RSS and native QQ resource readings are separate from JS heap. Native idle
reclaim observes descendant CPU, including QQ, rather than the idle supervisor
alone. The Windows soft working-set target is separately configurable through
`FISHBOT_MEMORY_LLBOT_MIB` (initially 192); these starting thresholds need real
QQ soak measurements. They are not measured memory guarantees or hard quotas.

## Verification scope

`npm run test:llbot` exercises local transport, distribution patches, actual
registry/reverse class, API permissions/CAS, dedicated/shared switching, process
replacement, receipts and resource decisions. `npm run test:llbot:web` serves a
private local API and verifies real browser creation/queued switching and mobile
layout. The runtime/patch probes require the reviewed research archive extract;
when absent, those probes explicitly skip. Native QQ is never run by tests.
The native PMHQ local-auth preflight was additionally run with an inert QQ file:
it returned `auth_token OK`, then failed to start that deliberately invalid file.
That proves the local authentication exchange, not QQ login or injection.

This is implemented/offline-verified, **not real-QQ certified**. QQNT profile and
job membership, license compatibility, true proxy routing/fallback, captcha/
phone-confirm phases, uploads, avatar/voice/recall variants, Qzone visibility and
unknown-operation readback still require an authorized test account. The current
LLBot login API exposes QR/online but not all intermediate phone verification
phases; the platform does not fabricate those states. Production remains NapCat
until native checks and a sustained mixed-Bot soak are completed.
