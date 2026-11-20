"""Bounded startup callback recovery for the pinned NapCat 4.18.41 shell."""
from pathlib import Path
import sys

ORIGINAL = 't.addKernelLoginListener(cu(c, e)), t.connect(), await a;'
LEGACY_MARKER = '/* fishbot-startup-login-callback-v1 */'
LEGACY = ('t.addKernelLoginListener(cu(c, e)), t.connect(), ' + LEGACY_MARKER
          + ' setTimeout(() => { if (!o.isLogined && !o.loginFlowInitialized && t.getMsfStatus() === 1) c.onLoginConnected(); }, 2000), await a;')
MARKER = '/* fishbot-startup-login-callback-v2 */'
PATCHED = ('o.startupCallback = c.onLoginConnected, '
           'c.onLoginConnected = (...args) => { o.startupCallbackSeen = true; return o.startupCallback(...args); }, '
           't.addKernelLoginListener(cu(c, e)), t.connect(), '
           + MARKER + ' setTimeout(() => { '
           'if (!o.startupCallbackSeen && !o.isLogined && !o.loginFlowInitialized && t.getMsfStatus() === 1) '
           'c.onLoginConnected(); }, 2000), await a;')

# The native process remains alive until the existing Agent command ledger
# accepts the request. No child, watcher or replacement scheduler is created.
RECOVERY_PATCHES = [
    ('async function FL() {', 'async function FL() {\n'
     '  /* fishbot-single-process-recovery-v1 */\n'
     '  if (process.env.NAPCAT_PLATFORM_MANIFEST && process.env.NAPCAT_DISABLE_MULTI_PROCESS === "1")\n'
     '    pe.setRestartProcessCall(async () => { Ke.FishBotRestartRequested = true; return { result: true, message: "恢复请求已交给协议托管器" }; });'),
    ('loginError: pe.getQQLoginError()\n',
     'loginError: pe.getQQLoginError(),\n    restartRequested: Ke.FishBotRestartRequested === true\n'),
    ('setQQLoginStatus(t) {\n',
     'setQQLoginStatus(t) {\n    if (t) Ke.FishBotRestartRequested = false;\n'),
]


def patch_recovery(source):
    for original, patched in RECOVERY_PATCHES:
        if source.count(patched) == 1:
            continue
        if source.count(original) != 1:
            raise ValueError('Unsupported NapCat recovery flow; refusing a guessed patch')
        source = source.replace(original, patched)
    return source


def patch_login(file):
    file = Path(file)
    source = file.read_text(encoding='utf-8')
    before = source
    if LEGACY_MARKER in source:
        if source.count(LEGACY) != 1:
            raise ValueError('Unexpected legacy NapCat login patch')
        source = source.replace(LEGACY, ORIGINAL)
    if MARKER in source:
        if source.count(PATCHED) != 1:
            raise ValueError('Unexpected existing NapCat login patch')
    else:
        if source.count(ORIGINAL) != 1 or 'o.loginFlowInitialized = !0' not in source:
            raise ValueError('Unsupported NapCat login flow; refusing a guessed patch')
        source = source.replace(ORIGINAL, PATCHED)
    source = patch_recovery(source)
    if source == before:
        return False
    file.write_text(source, encoding='utf-8')
    return True


if __name__ == '__main__':
    patch_login(sys.argv[1])
