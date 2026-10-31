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


def patch_login(file):
    file = Path(file)
    source = file.read_text(encoding='utf-8')
    if LEGACY_MARKER in source:
        if source.count(LEGACY) != 1:
            raise ValueError('Unexpected legacy NapCat login patch')
        source = source.replace(LEGACY, ORIGINAL)
    if MARKER in source:
        if source.count(PATCHED) != 1:
            raise ValueError('Unexpected existing NapCat login patch')
        return False
    if source.count(ORIGINAL) != 1 or 'o.loginFlowInitialized = !0' not in source:
        raise ValueError('Unsupported NapCat login flow; refusing a guessed patch')
    file.write_text(source.replace(ORIGINAL, PATCHED), encoding='utf-8')
    return True


if __name__ == '__main__':
    patch_login(sys.argv[1])
