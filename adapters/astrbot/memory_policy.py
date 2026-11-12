"""Shared summary policy constants for memory admission and checkpoints."""
import json
from pathlib import Path


_memory_defaults=json.loads((Path(__file__).resolve().parents[2]/'packages/contracts/memory-defaults.json').read_text(encoding='utf-8'))
DEFAULT_SUMMARY_ROUNDS=_memory_defaults['summaryRounds']
CONTINUITY_MAX_CHARS=500
