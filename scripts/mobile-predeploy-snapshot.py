"""Read-only source projection for an OFFLINE predeployment, never a cutover image.

Only durable cognitive products/configuration are copied. No active Journal,
raw messages, session profiles, or external-execution state is exported here.
The final frozen snapshot must replace these projections before Agent startup.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import sqlite3
import subprocess
from urllib.parse import urlparse

COGNITIVE_TABLES = {
    'facts', 'fact_metadata', 'relations', 'relation_progress',
    'memory_agreements', 'memory_core_notes', 'memory_summaries',
    'memory_summary_origins', 'memory_summary_levels', 'memory_summary_people',
    'memory_reflection_control', 'memory_reflection_progress', 'memory_blocks',
    'social_pattern_blocks', 'social_control', 'social_patterns',
    'expression_reinforcement', 'social_learning',
    'memory_reconstruction_checkpoints', 'memory_checkpoint_admin_overrides',
    'memory_semantic_batches', 'memory_semantic_discard_audit',
    'memory_hold_rounds', 'memory_summary_unresolved',
    'memory_media_retirement_choices', 'memory_retirement_exceptions',
    'sticker_inbox', 'sticker_originals',
}
RAW_TABLES = {
    'fishbot_readonly_journals', 'fishbot_readonly_journal_blobs',
    'memory_rounds', 'memory_pending', 'memory_pending_rounds',
    'memory_round_material', 'memory_message_sources', 'memory_checkpoint_archives',
    'fishbot_operator_session_archives', 'social_samples', 'fishbot_shared_inputs',
    'pipeline_work_inputs', 'pipeline_quote_sources', 'platform_message_history',
    'contacts',  # last_text/last_output are live dialogue, not durable products.
}


def sha(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def project_sqlite(source, destination):
    """One source read transaction, schema unchanged, explicit row allowlist."""
    destination = Path(destination)
    if destination.exists():
        raise FileExistsError(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(Path(source).resolve().as_uri() + '?mode=ro', uri=True)
    dst = sqlite3.connect(destination)
    try:
        src.execute('PRAGMA query_only=ON')
        src.execute('BEGIN')
        schema = src.execute("SELECT type,name,sql FROM sqlite_master WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%' ORDER BY rowid").fetchall()
        tables = {name for kind, name, sql in schema if kind == 'table'}
        # CREATE VIRTUAL TABLE creates its shadow tables itself. Replaying their
        # separate CREATE statements duplicates them; their raw index rows are
        # never part of the cognitive projection.
        shadows = {row[1] for row in src.execute('PRAGMA table_list') if row[2] == 'shadow'}
        for kind, name, sql in schema:
            if kind == 'table' and name not in shadows:
                dst.execute(sql)
        counts = {}
        committed_checkpoints = "SELECT id FROM memory_reconstruction_checkpoints WHERE summary_id IN (SELECT id FROM memory_summaries) AND state IN ('available','reserved','consumed')"
        for name in sorted(COGNITIVE_TABLES & tables):
            where = ''
            if name == 'memory_reconstruction_checkpoints':
                where = " WHERE summary_id IN (SELECT id FROM memory_summaries) AND state IN ('available','reserved','consumed')"
            if name == 'memory_semantic_batches':
                where = " WHERE state IN ('committed','retired') AND checkpoint_id IN (" + committed_checkpoints + ')'
            if name == 'memory_checkpoint_admin_overrides':
                where = ' WHERE checkpoint_id IN (' + committed_checkpoints + ')'
            if name == 'sticker_inbox':
                where = " WHERE status='approved'"
            if name == 'sticker_originals':
                where = " WHERE id IN (SELECT id FROM sticker_inbox WHERE status='approved')"
            rows = src.execute('SELECT * FROM "' + name + '"' + where).fetchall()
            if rows:
                dst.executemany('INSERT INTO "' + name + '" VALUES(' + ','.join('?' for _ in rows[0]) + ')', rows)
            counts[name] = len(rows)
        # Preserve ID high-water marks without copying raw rows.
        if src.execute("SELECT 1 FROM sqlite_master WHERE name='sqlite_sequence'").fetchone():
            dst.execute('DELETE FROM sqlite_sequence')
            dst.executemany('INSERT INTO sqlite_sequence(name,seq) VALUES(?,?)', src.execute('SELECT name,seq FROM sqlite_sequence'))
        for kind, name, sql in schema:
            if kind != 'table':
                dst.execute(sql)
        dst.commit()
        assert dst.execute('PRAGMA quick_check').fetchone() == ('ok',)
        assert not dst.execute('PRAGMA foreign_key_check').fetchall()
        assert all(dst.execute('SELECT count(*) FROM "' + t + '"').fetchone()[0] == 0 for t in RAW_TABLES & tables)
        return {'rows': counts, 'bytes': destination.stat().st_size, 'sha256': sha(destination),
                'rawTablesEmpty': True, 'executable': False}
    finally:
        dst.close()
        src.close()


def project_assets(source, destination, account):
    """Only typed, referenced Social assets; never scan a QQ profile/cache."""
    source, destination = Path(source), Path(destination)
    copied = []
    def copy(relative, captured=None):
        p = source / relative
        if p.is_symlink() or not p.resolve().is_relative_to(source.resolve()):
            raise ValueError('asset_path_outside_bot')
        data = p.read_bytes() if captured is None else captured
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        copied.append(str(relative))
    settings = source / 'meme-pack-settings.json'
    if settings.is_file():
        copy(Path('meme-pack-settings.json'))
    index = source / 'qq-favorites/index.json'
    if index.is_file():
        if index.is_symlink() or not index.resolve().is_relative_to(source.resolve()):
            raise ValueError('asset_path_outside_bot')
        captured = index.read_bytes()
        data = json.loads(captured)
        if str(data.get('account', '')) == str(account) and account:
            # Keep the account's management metadata; files must be exact index
            # references, not arbitrary paths supplied by item text/URLs.
            copy(Path('qq-favorites/index.json'), captured)
            for item in data.get('items', [])[:200]:
                identifier, filename = item.get('id', ''), item.get('file', '')
                if not re.fullmatch(r'qqf_[a-f0-9]{16}', identifier) or filename not in (identifier + '.gif', identifier + '.png'):
                    continue
                relative = Path('qq-favorites/images') / filename
                if (source / relative).is_file() and (source / relative).stat().st_size <= 1048576:
                    copy(relative)
    for category in ('reactions', 'gallery'):
        folder = source / category
        if not folder.is_dir():
            continue
        for metadata in sorted(folder.glob('*.json'))[:64]:
            if metadata.is_symlink() or not metadata.resolve().is_relative_to(source.resolve()):
                raise ValueError('asset_path_outside_bot')
            captured = metadata.read_bytes()
            data = json.loads(captured)
            identifier = data.get('id', '')
            if not isinstance(identifier, str) or not re.fullmatch(r'[a-z0-9_-]{1,32}', identifier):
                continue
            image = folder / (identifier + '.png')
            if image.is_file() and image.stat().st_size <= 262144:
                copy(metadata.relative_to(source), captured)
                copy(image.relative_to(source))
    return copied


def capture(output, runtime, api_container, pg_container):
    output = Path(output)
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    os.umask(0o077)
    def run(args):
        return subprocess.check_output(args, timeout=120)
    env = dict(s.split('=', 1) for s in json.loads(run(['docker', 'inspect', api_container]))[0]['Config']['Env'] if '=' in s)
    dbname = urlparse(env['DATABASE_URL']).path.lstrip('/')
    def psql(sql):
        return run(['docker', 'exec', pg_container, 'sh', '-c', 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d ' + shlex.quote(dbname) + ' -At -c ' + shlex.quote(sql)])
    live = 'SELECT id FROM bots WHERE deleted_at IS NULL'
    selections = {
        'schema_migrations': 'SELECT * FROM schema_migrations',
        'users': 'SELECT * FROM users', 'tenants': 'SELECT * FROM tenants',
        'memberships': 'SELECT * FROM memberships',
        'invitations': 'SELECT * FROM invitations',
        'invitation_redemptions': 'SELECT * FROM invitation_redemptions',
        'runtime_groups': 'SELECT * FROM runtime_groups WHERE id IN (SELECT group_id FROM bots WHERE deleted_at IS NULL)',
        'bots': 'SELECT * FROM bots WHERE deleted_at IS NULL',
        'bot_config_revisions': 'SELECT c.* FROM bot_config_revisions c JOIN bots b ON b.id=c.bot_id AND b.revision=c.revision WHERE b.deleted_at IS NULL',
        'audit_events': "SELECT DISTINCT ON (a.bot_id) a.* FROM audit_events a JOIN bots b ON b.id=a.bot_id WHERE b.deleted_at IS NULL AND a.event='config.saved' AND a.detail->>'source'='authenticated_web_config' AND a.detail->>'actorId' IS NOT NULL AND a.detail->>'revision'=b.revision::text ORDER BY a.bot_id,a.id DESC",
    }
    for table in ('secrets', 'qq_bindings', 'group_members', 'autonomy_goals', 'bilibili_settings', 'bilibili_accounts'):
        selections[table] = 'SELECT * FROM ' + table + ' WHERE bot_id IN (' + live + ')'
    # One statement = one PostgreSQL MVCC snapshot, not per-table wall clocks.
    pairs = ["'" + name + "',COALESCE((SELECT jsonb_agg(to_jsonb(t)) FROM (" + sql + ") t),'[]'::jsonb)" for name, sql in selections.items()]
    data = json.loads(psql('SELECT jsonb_build_object(' + ','.join(pairs) + ')'))
    (output / 'stable-pg.json').write_text(json.dumps(data), encoding='utf-8')
    (output / 'schema.sql').write_bytes(run(['docker', 'exec', pg_container, 'sh', '-c', 'pg_dump -U "$POSTGRES_USER" -d ' + shlex.quote(dbname) + ' --schema-only --no-owner --no-privileges']))
    root = Path(runtime)
    key = root / 'master.key'
    (output / 'master.key').write_bytes(key.read_bytes())
    result = {'phase': 'stable-only-not-cutover', 'executionAuthorized': False,
              'pgCounts': {t: len(rows) for t, rows in data.items()}, 'bots': [], 'missingBotStores': [],
              'deferred': ['current Journal and originals', 'QQ session profiles',
                           'Work/effects/unknown/partial ledgers', 'current autonomous state',
                           'referenced private media', 'final cross-store frozen snapshot']}
    for bot in data['bots']:
        source = root / 'bots' / bot['id'] / 'behavior.sqlite'
        if source.exists():
            relative = 'bots/' + bot['id'] + '/behavior.sqlite'
            proof = project_sqlite(source, output / relative)
            proof.update(path=relative, botId=bot['id'], botHash=hashlib.sha256(bot['id'].encode()).hexdigest()[:16], sourceBytes=source.stat().st_size)
            result['bots'].append(proof)
        else:
            result['missingBotStores'].append({'botHash': hashlib.sha256(bot['id'].encode()).hexdigest()[:16],
                                               'desiredState': bot['desired_state']})
        binding = next((row['qq_uin'] for row in data['qq_bindings'] if row['bot_id'] == bot['id']), bot['expected_uin'])
        assets = project_assets(root / 'bots' / bot['id'], output / 'bots' / bot['id'], binding)
        if assets:
            result.setdefault('assets', []).append({'botHash': hashlib.sha256(bot['id'].encode()).hexdigest()[:16], 'files': len(assets)})
    result['files'] = {str(p.relative_to(output)): {'bytes': p.stat().st_size, 'sha256': sha(p)} for p in output.rglob('*') if p.is_file()}
    (output / 'manifest-private.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps({'phase': result['phase'], 'pgCounts': result['pgCounts'], 'bots': len(result['bots']), 'bytes': sum(x['bytes'] for x in result['files'].values()), 'executionAuthorized': False}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    parser.add_argument('--runtime-root', required=True)
    parser.add_argument('--api-container', default='fishbot-production-api')
    parser.add_argument('--pg-container', default='fishbot-production-pg')
    args = parser.parse_args()
    capture(args.output, args.runtime_root, args.api_container, args.pg_container)
