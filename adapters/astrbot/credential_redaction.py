"""Shared credential grammar; callers retain their intentional thresholds."""
import re

def pattern(minimum=8,bearer=True):
    return re.compile(r'\bsk-[A-Za-z0-9_-]{'+str(minimum)+r',}'+(r'|Bearer\s+\S+' if bearer else ''),re.I)

def redact(value,minimum=12,replacement='[凭据已移除]'):
    return pattern(minimum).sub(replacement,value)
