"""Automated Python architecture boundary invariants for FishBot AI runtime."""
import ast
import pathlib
import re
import subprocess
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
ASTRBOT_DIR = ROOT / 'adapters' / 'astrbot'

RETIRED_TABLES = (
    'fact_vectors',
    'memory_vectors',
    'memory_hold_rounds',
    'memory_summary_coverage',
    'memory_media_retirement_choices',
    'memory_retirement_exceptions',
    'memory_summary_unresolved',
    'memory_reflection_budget',
    'memory_web_context_permits',
    'companion_plans',
    'social_images',
    'expression_usage',
    'initiative_checks',
    'initiative_schedule',
)


def _tarjan(graph):
    idx = 0
    stack = []
    on = set()
    low = {}
    disc = {}
    sccs = []

    def dfs(v):
        nonlocal idx
        disc[v] = low[v] = idx
        idx += 1
        stack.append(v)
        on.add(v)
        for w in graph[v]:
            if w not in disc:
                dfs(w)
                low[v] = min(low[v], low[w])
            elif w in on:
                low[v] = min(low[v], disc[w])
        if low[v] == disc[v]:
            comp = []
            while True:
                w = stack.pop()
                on.remove(w)
                comp.append(w)
                if w == v:
                    break
            if len(comp) > 1:
                sccs.append(sorted(comp))

    for v in graph:
        if v not in disc:
            dfs(v)
    return sccs


class ArchitectureBoundaryTests(unittest.TestCase):
    def test_zero_python_import_sccs(self):
        mods = {p.stem: p for p in ASTRBOT_DIR.glob('*.py')}
        adj_top = {m: set() for m in mods}
        adj_all = {m: set() for m in mods}

        def walk(mod_name, node, top=True):
            for child in ast.iter_child_nodes(node):
                is_fn = isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                if isinstance(child, ast.Import):
                    for a in child.names:
                        target = a.name.split('.')[0]
                        if target in mods:
                            adj_all[mod_name].add(target)
                            if top:
                                adj_top[mod_name].add(target)
                elif isinstance(child, ast.ImportFrom) and child.module:
                    target = child.module.split('.')[0]
                    if target in mods:
                        adj_all[mod_name].add(target)
                        if top:
                            adj_top[mod_name].add(target)
                walk(mod_name, child, top and not is_fn)

        for mod_name, path in mods.items():
            tree = ast.parse(path.read_text('utf-8'))
            walk(mod_name, tree, True)
            adj_top[mod_name].discard(mod_name)
            adj_all[mod_name].discard(mod_name)

        self.assertEqual(_tarjan(adj_top), [], 'Top-level Python imports must be strictly acyclic')
        self.assertEqual(_tarjan(adj_all), [], 'All Python imports (including lazy imports) must form a strict DAG')

    def test_context_protocol_stays_layer0_pure(self):
        code = (
            'import sys; sys.path.insert(0, %r); '
            'import context_protocol; '
            'forbidden = [m for m in ("sqlite3", "astrbot", "behavior", "summary_material", "readonly_journal") if m in sys.modules]; '
            'assert not forbidden, forbidden'
        ) % str(ASTRBOT_DIR)
        result = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_core_note_single_writer_ownership(self):
        write_re = re.compile(r'(?:CREATE\s+TABLE|INSERT|UPDATE|DELETE)\b[^\n;]*\bmemory_core_notes\b', re.I)
        violators = []
        for path in sorted(ASTRBOT_DIR.glob('*.py')):
            if path.name == 'core_note.py':
                continue
            text = path.read_text('utf-8')
            if write_re.search(text):
                violators.append(path.name)
        self.assertEqual(violators, [], f'Only core_note.py may write to memory_core_notes; found: {violators}')

    def test_embedding_budget_single_owner(self):
        write_re = re.compile(r'(?:CREATE\s+TABLE|INSERT|UPDATE|DELETE)\b[^\n;]*\bmemory_embedding_budget\b', re.I)
        violators = []
        for path in sorted(ASTRBOT_DIR.glob('*.py')):
            if path.name == 'embedding_client.py':
                continue
            text = path.read_text('utf-8')
            if write_re.search(text):
                violators.append(path.name)
        self.assertEqual(violators, [], f'Only embedding_client.py may own memory_embedding_budget; found: {violators}')

    def test_retired_tables_not_recreated(self):
        violators = []
        for path in sorted(ASTRBOT_DIR.glob('*.py')):
            text = path.read_text('utf-8')
            for table in RETIRED_TABLES:
                if re.search(rf'CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?{table}\b', text, re.I):
                    violators.append(f'{path.name}:{table}')
        self.assertEqual(violators, [], f'Retired tables must never be recreated: {violators}')

    def test_ai_runtime_does_not_mutate_napcat_or_egress_internals(self):
        violators = []
        for path in sorted(ASTRBOT_DIR.glob('*.py')):
            text = path.read_text('utf-8')
            if 'napcat-platform.json' in text or 'NAPCAT_EGRESS_PROXY_PORT' in text or 'SetQuickLogin' in text:
                violators.append(path.name)
        self.assertEqual(violators, [], f'AI runtime must not access NapCat/egress internals: {violators}')


if __name__ == '__main__':
    unittest.main()
