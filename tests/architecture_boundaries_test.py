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

    def test_single_owner_sqlite_tables(self):
        owners = {
            'memory_core_notes': 'core_note.py',
            'memory_embedding_budget': 'embedding_client.py',
            'qq_favorite_writes': 'qq_favorite_tools.py',
            'memory_agreements': 'agreement_state.py',
            'memory_round_material': 'summary_material.py',
            'companion_motives': 'companion_state.py',
            'relations': 'relationships.py',
            'relation_evidence': 'relationships.py',
            'autonomy_groups': 'autonomy.py',
            'memory_blocks': 'memory_scope.py',
            'memory_message_sources': 'message_provenance.py',
        }
        violators = []
        for path in sorted(ASTRBOT_DIR.glob('*.py')):
            source = path.read_text('utf-8')
            tree = ast.parse(source)
            # 1. Inspect every SQL string literal (including multiline strings) for write DDL/DML.
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    for stmt in node.value.split(';'):
                        for table, owner in owners.items():
                            if path.name == owner:
                                continue
                            if re.search(rf'(?:CREATE\s+TABLE|INSERT(?:\s+OR\s+\w+)?\s+INTO|UPDATE|DELETE\s+FROM)\b[^;]*?\b{table}\b', stmt, re.I | re.S):
                                violators.append(f'{table}:{path.name}:{node.lineno} (owner={owner})')
            # 2. Inspect dynamic `for <var> in <iterable>:` loops whose body runs DELETE/UPDATE on `<var>`.
            assigned_tuples = {}
            for node in ast.walk(tree):
                if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                    if isinstance(node.value, (ast.Tuple, ast.List)):
                        lits = [e.value for e in node.value.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]
                        if lits:
                            assigned_tuples[node.targets[0].id] = lits
            for node in ast.walk(tree):
                if isinstance(node, ast.For) and isinstance(node.target, ast.Name):
                    loop_var = node.target.id
                    tables_in_loop = []
                    if isinstance(node.iter, (ast.Tuple, ast.List)):
                        tables_in_loop = [e.value for e in node.iter.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]
                    elif isinstance(node.iter, ast.Name) and node.iter.id in assigned_tuples:
                        tables_in_loop = assigned_tuples[node.iter.id]
                    if not tables_in_loop:
                        continue
                    body_src = ast.get_source_segment(source, node) or ''
                    if re.search(rf"""(?:DELETE\s+FROM\s+['"]\s*\+\s*{re.escape(loop_var)}|UPDATE\s+['"]\s*\+\s*{re.escape(loop_var)})""", body_src, re.I):
                        for table in tables_in_loop:
                            owner = owners.get(table)
                            if owner and path.name != owner:
                                violators.append(f'{table}:dynamic:{path.name}:{node.lineno} (owner={owner})')
        self.assertEqual(violators, [], f'Single-owner SQLite table violations: {violators}')

    def test_no_cross_module_private_store_journal_access(self):
        pat = re.compile(r'\b(store|st|journal|j)\._([a-zA-Z][a-zA-Z0-9_]*)')
        target_names = {'store', 'st', 'journal', 'j'}
        violators = []
        for path in sorted(ASTRBOT_DIR.glob('*.py')):
            source = path.read_text('utf-8')
            for i, line in enumerate(source.splitlines(), 1):
                if line.lstrip().startswith('#'):
                    continue
                for m in pat.finditer(line):
                    violators.append(f'{path.name}:{i}:{m.group(0)}')
            tree = ast.parse(source)
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ('getattr', 'setattr', 'hasattr'):
                    if len(node.args) >= 2 and isinstance(node.args[0], ast.Name) and node.args[0].id in target_names:
                        attr = node.args[1]
                        if isinstance(attr, ast.Constant) and isinstance(attr.value, str) and attr.value.startswith('_') and not attr.value.startswith('__'):
                            violators.append(f'{path.name}:{node.lineno}:{node.func.id}({node.args[0].id}, {attr.value!r})')
        self.assertEqual(violators, [], f'Cross-module private store/journal attribute access found: {violators}')

    def test_legacy_hold_rounds_migrated_not_silently_dropped(self):
        import json, sqlite3
        if str(ASTRBOT_DIR) not in sys.path:
            sys.path.insert(0, str(ASTRBOT_DIR))
        from memory_reflection import Reflection
        db = sqlite3.connect(':memory:')
        db.execute('CREATE TABLE memory_hold_rounds(scope TEXT,event TEXT,round_id INTEGER,reason TEXT,created REAL,PRIMARY KEY(scope,event))')
        db.execute("INSERT INTO memory_hold_rounds VALUES('group:12345','ev-old-1',7,'missing_full_original',1700000000.0)")
        db.execute('CREATE TABLE memory_summary_unresolved(scope TEXT,event TEXT,summary_id INTEGER,digest TEXT)')
        db.execute("INSERT INTO memory_summary_unresolved VALUES('private:99999','ev-unres-2',3,'abc')")
        db.execute("INSERT INTO memory_summary_unresolved VALUES('private:99999','ev-unres-2',4,'def')")
        db.commit()
        Reflection(db)
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertNotIn('memory_hold_rounds', tables)
        self.assertNotIn('memory_summary_unresolved', tables)
        issues = db.execute('SELECT scope,checkpoint_id,kind,status,detail FROM memory_checkpoint_issues ORDER BY kind').fetchall()
        self.assertEqual(len(issues), 3)
        self.assertEqual(issues[0][0], 'group:12345')
        self.assertEqual(issues[0][2], 'legacy_hold_round:ev-old-1')
        self.assertEqual(issues[0][3], 'open')
        d0 = json.loads(issues[0][4])
        self.assertEqual(d0['reason'], 'missing_full_original')
        self.assertEqual(d0['round_id'], 7)
        self.assertEqual(issues[1][0], 'private:99999')
        self.assertEqual(issues[1][1], 3)
        self.assertEqual(issues[1][2], 'legacy_summary_unresolved:ev-unres-2:3')
        self.assertEqual(json.loads(issues[1][4])['digest'], 'abc')
        self.assertEqual(issues[2][1], 4)
        self.assertEqual(issues[2][2], 'legacy_summary_unresolved:ev-unres-2:4')
        self.assertEqual(json.loads(issues[2][4])['digest'], 'def')
        db.close()

        # Unknown legacy schema with rows must never be silently dropped.
        db2 = sqlite3.connect(':memory:')
        db2.execute('CREATE TABLE memory_hold_rounds(unexpected_col TEXT)')
        db2.execute("INSERT INTO memory_hold_rounds VALUES('keep-me')")
        db2.commit()
        Reflection(db2)
        tables2 = {r[0] for r in db2.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertIn('memory_hold_rounds', tables2)
        self.assertEqual(db2.execute('SELECT unexpected_col FROM memory_hold_rounds').fetchall(), [('keep-me',)])
        db2.close()

    def test_legacy_hold_migration_rolls_back_copy_and_drop_together(self):
        import sqlite3
        sys.path.insert(0, str(ASTRBOT_DIR)) if str(ASTRBOT_DIR) not in sys.path else None
        from memory_reflection import Reflection
        db = sqlite3.connect(':memory:')
        self.addCleanup(db.close)
        db.execute('CREATE TABLE memory_hold_rounds(scope,event,round_id,reason,created)')
        db.execute("INSERT INTO memory_hold_rounds VALUES('group:12345','held',9,'pending',1)")
        db.execute('CREATE TABLE memory_summary_unresolved(scope,event,summary_id,digest)')
        db.execute("INSERT INTO memory_summary_unresolved VALUES('group:12345','unresolved',3,'digest')")
        db.commit()
        def deny_second_drop(action, table, *_):
            return sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_DROP_TABLE and table == 'memory_summary_unresolved' else sqlite3.SQLITE_OK
        db.set_authorizer(deny_second_drop)
        with self.assertRaises(sqlite3.DatabaseError):
            Reflection(db)
        db.set_authorizer(None)
        self.assertEqual(db.execute('SELECT event FROM memory_hold_rounds').fetchall(), [('held',)])
        self.assertEqual(db.execute('SELECT event FROM memory_summary_unresolved').fetchall(), [('unresolved',)])
        self.assertEqual(db.execute('SELECT count(*) FROM memory_checkpoint_issues').fetchone()[0], 0)
        Reflection(db)
        self.assertEqual(db.execute('SELECT count(*) FROM memory_checkpoint_issues').fetchone()[0], 2)

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
