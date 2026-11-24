"""The reviewed local package's native bridge, restricted to one owned PMHQ.

Never enumerates or opens other PMHQ processes, changes hosts, or owns port 443.
The parent supplies a live child identity and keeps this pipe open for its life.
"""
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import sys

import psutil


def serve(spec):
    if os.name != 'nt':
        raise RuntimeError('Local PMHQ bridge requires Windows')
    process = psutil.Process(spec['pid'])
    executable = Path(spec['executable']).resolve()
    assets = Path(spec['assets']).resolve()

    def owner():
        if (process.ppid() != spec['parent'] or abs(process.create_time()-spec['created']) > .02
                or Path(process.exe()).resolve() != executable):
            raise RuntimeError('PMHQ ownership changed')

    owner()
    api = ctypes.WinDLL('kernel32', use_last_error=True)
    api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    api.OpenProcess.restype = wintypes.HANDLE
    api.ReadProcessMemory.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
    api.WriteProcessMemory.argtypes = api.ReadProcessMemory.argtypes
    api.VirtualProtectEx.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_size_t, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    api.FlushInstructionCache.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_size_t]
    api.CloseHandle.argtypes = [wintypes.HANDLE]

    class Region(ctypes.Structure):
        _fields_ = [('base', ctypes.c_void_p), ('allocation', ctypes.c_void_p), ('allocationProtect', wintypes.DWORD),
                    ('partition', wintypes.WORD), ('size', ctypes.c_size_t), ('state', wintypes.DWORD),
                    ('protect', wintypes.DWORD), ('kind', wintypes.DWORD)]

    api.VirtualQueryEx.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.POINTER(Region), ctypes.c_size_t]
    api.VirtualQueryEx.restype = ctypes.c_size_t
    handle = api.OpenProcess(0x0438, False, process.pid)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    pattern = (assets/'isrg-spki-pattern.bin').read_bytes()
    replacement = (assets/'spki-inner-546.der').read_bytes()
    if len(pattern) != 64 or len(replacement) != 546:
        raise RuntimeError('Unexpected reviewed native material')
    changes = []

    def read(address, size):
        data = ctypes.create_string_buffer(size)
        length = ctypes.c_size_t()
        if not api.ReadProcessMemory(handle, address, data, size, ctypes.byref(length)):
            return b''
        return data.raw[:length.value]

    def write(address, value):
        owner()
        previous = wintypes.DWORD()
        if not api.VirtualProtectEx(handle, address, len(value), 0x40, ctypes.byref(previous)):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            length = ctypes.c_size_t()
            data = ctypes.create_string_buffer(value)
            if not api.WriteProcessMemory(handle, address, data, len(value), ctypes.byref(length)) or length.value != len(value):
                raise RuntimeError('Native bridge write incomplete')
            api.FlushInstructionCache(handle, address, len(value))
            if read(address, len(value)) != value:
                raise RuntimeError('Native bridge verification failed')
        finally:
            restored = wintypes.DWORD()
            if not api.VirtualProtectEx(handle, address, len(value), previous.value, ctypes.byref(restored)):
                raise ctypes.WinError(ctypes.get_last_error())

    def patch():
        owner()
        if changes:
            if any(read(address, len(value)) != value for address, _, value in changes):
                raise RuntimeError('Native bridge changed unexpectedly')
            return
        address = 0
        while address < 0x7FFFFFFEFFFF:
            region = Region()
            if not api.VirtualQueryEx(handle, address, ctypes.byref(region), ctypes.sizeof(region)):
                break
            start = region.base or 0
            if region.size <= 0:
                break
            if region.state == 0x1000 and region.size < 128*1024*1024 and region.protect & 0xFF not in (0, 1) and not region.protect & 0x100:
                raw = read(start, region.size)
                offset = raw.find(pattern)
                if offset >= 0:
                    anchor = start+offset
                    image = anchor-0x37EFD8
                    branch = image+0x11FD03
                    original = read(branch, 2)
                    if read(image, 2) != b'MZ' or len(original) != 2 or original[0] not in (0x74, 0x75):
                        raise RuntimeError('Reviewed PMHQ image layout mismatch')
                    anchor_original = read(anchor, len(replacement))
                    if len(anchor_original) != len(replacement) or not anchor_original.startswith(pattern):
                        raise RuntimeError('Reviewed native anchor mismatch')
                    changes.extend([(anchor, anchor_original, replacement), (branch, original, b'\x90\x90')])
                    for at, _, value in changes:
                        write(at, value)
                    return
            address = start+region.size
        raise RuntimeError('Reviewed PMHQ native material not ready')

    try:
        for line in sys.stdin:
            command = json.loads(line)
            if command.get('op') != 'patch':
                raise RuntimeError('Unknown bridge operation')
            try:
                patch()
                print(json.dumps({'ok': True}), flush=True)
            except Exception as error:
                print(json.dumps({'ok': False, 'error': str(error)[:160]}), flush=True)
    finally:
        try:
            for address, original, value in reversed(changes):
                if read(address, len(value)) == value:
                    write(address, original)
        except (OSError, RuntimeError, psutil.Error):
            pass  # The owned PMHQ may already have exited; never open a reused PID.
        api.CloseHandle(handle)


if __name__ == '__main__':
    try:
        serve(json.loads(sys.argv[1]))
    except Exception as error:
        print(json.dumps({'ok': False, 'error': str(error)[:160]}), flush=True)
        sys.exit(1)
