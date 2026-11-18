"""On-demand, bounded metadata reader. No runtime imports or writes."""
from contextlib import closing
import json
import math
import re
import sqlite3
import sys
import time
from pathlib import Path


def number(value):
    return value if type(value) is int and 0 <= value <= 2**53 - 1 else None


def read_statistics(root, bot_id):
    folder = Path(root) / 'bots' / bot_id
    rounds = {'count': None, 'threshold': 30, 'status': 'unavailable'}
    database = folder / 'behavior.sqlite'
    if database.is_file():
        try:
            with closing(sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True, timeout=1)) as db:
                db.execute('PRAGMA query_only=ON')
                deadline = time.monotonic() + 2
                db.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
                # Same snapshot and definition as Reflection.health_snapshot.
                db.execute('BEGIN')
                if not db.execute("SELECT 1 FROM fishbot_shared_context WHERE id=1 AND state='active'").fetchone():
                    raise sqlite3.DatabaseError('shared_journal_not_active')
                progress = db.execute('SELECT summary_watermark FROM memory_reflection_progress WHERE scope=?', ('bot_shared',)).fetchone()
                count = db.execute('SELECT count(*) FROM memory_rounds WHERE scope=? AND id>?', ('bot_shared', progress[0] if progress else 0)).fetchone()[0]
                rounds.update(count=count, status='available')
        except sqlite3.Error:
            pass

    wires, results = {}, {}
    incomplete = False
    # Tail each retained diagnostic file, never inspect captures or message bodies.
    archives = sorted(folder.glob('model-diagnostics.previous.*.jsonl'), reverse=True)[:3]
    for file in list(reversed(archives)) + [folder / 'model-diagnostics.jsonl']:
        try:
            with file.open('rb') as stream:
                size = stream.seek(0, 2)
                start = max(0, size - 2 * 1024 * 1024)
                stream.seek(start)
                if start:
                    stream.readline()
                    incomplete = True
                data = stream.read(2 * 1024 * 1024)
        except FileNotFoundError:
            continue
        except OSError:
            incomplete = True
            continue
        for line in data.splitlines():
            try:
                row = json.loads(line)
            except (ValueError, UnicodeError):
                incomplete = True
                continue
            if not isinstance(row, dict) or row.get('botId') != bot_id:
                continue
            attempt, at = row.get('attemptId'), row.get('at')
            if not isinstance(attempt, str) or len(attempt) > 128 or type(at) not in (int, float) or not math.isfinite(at) or at <= 0:
                continue
            if row.get('kind') == 'wire_request' and number(row.get('wireIndex')) is not None:
                if number(row.get('textChars')) is not None:
                    wires[(attempt, row['wireIndex'])] = row
            elif row.get('kind') == 'request_result' and row.get('phase') != 'embedding' and not str(row.get('phase', '')).startswith('model_probe:') and (number(row.get('wireCount')) or 0) > 0:
                results[attempt] = row
    items = []
    for attempt, row in sorted(results.items(), key=lambda entry: entry[1]['at'], reverse=True)[:20]:
        wire = wires.get((attempt, row.get('wireCount')), {})
        safe = lambda value: value[:128] if isinstance(value, str) else None
        items.append({'id': attempt, 'at': row['at'], 'model': safe(row.get('model')),
                      'phase': safe(row.get('phase')), 'status': safe(row.get('status')),
                      'wireCount': number(row.get('wireCount')), 'inputTokens': number(row.get('inputTokens')),
                      'cachedTokens': number(row.get('cachedTokens')), 'textChars': number(wire.get('textChars')),
                      'toolsChars': number(wire.get('toolsChars')), 'imageCount': number(wire.get('imageCount'))})
    return {'sampledAt': time.time(), 'rounds': rounds, 'requests': items, 'partial': incomplete}


if __name__ == '__main__':
    if len(sys.argv) != 3 or not re.fullmatch(r'[a-fA-F0-9-]{36}', sys.argv[2]):
        raise SystemExit(2)
    print(json.dumps(read_statistics(sys.argv[1], sys.argv[2]), ensure_ascii=False))
