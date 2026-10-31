"""Short-lived, local-only data access. Platform authenticates the Bot first.

No AstrBot import/startup, Provider access, remote URLs or arbitrary SQL input.
The only path is supplied by the control plane, never by the browser.
"""
import json
from pathlib import Path
import sqlite3
import sys
import time


def initialize_controls(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS memory_blocks(scope TEXT,subject TEXT,name TEXT,PRIMARY KEY(scope,subject,name));
    CREATE TABLE IF NOT EXISTS social_pattern_blocks(scope TEXT,expression TEXT,PRIMARY KEY(scope,expression));
    CREATE TABLE IF NOT EXISTS social_control(id INTEGER PRIMARY KEY CHECK(id=1),epoch INTEGER NOT NULL);
    INSERT OR IGNORE INTO social_control VALUES(1,0);
    ''')


def run(file, data, connection=None):
    file = Path(file)
    if connection is None and not file.exists() and data['action'] not in ('list_stickers','get_sticker','review_sticker'):
        if data['action'] != 'list':
            return {'error': '机器人尚未产生行为数据', 'status': 409}
        return {'facts': [], 'patterns': [], 'contacts': [], 'relations': [], 'stats': {}, 'epoch': 0, 'initialized': False}
    if data['action'] in ('list_stickers','get_sticker','review_sticker') and not file.exists() and connection is None:
        return {'items':[],'pendingLimit':10,'approvedLimit':20} if data['action']=='list_stickers' else {'error':'表情包不存在','status':404}
    db = connection or sqlite3.connect(file.as_uri() + '?mode=rw', uri=True, timeout=3)
    old_factory=db.row_factory
    db.row_factory = sqlite3.Row
    try:
        if data['action'] in ('list_stickers','get_sticker','review_sticker'):
            from sticker_collection import manage
            return manage(db,data)
        if data['action']=='list':db.execute('BEGIN')
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if data['action'] == 'list':
            scope, subject, query = data.get('scope', ''), data.get('subject', ''), data.get('query', '')
            clauses, args = [], []
            for key, value in [('scope', scope), ('subject', subject)]:
                if value:
                    clauses.append(key + '=?')
                    args.append(value)
            fact_where = ' AND '.join(['active=1'] + clauses)
            if query:
                fact_where += ' AND (instr(name,?)>0 OR instr(value,?)>0)'
                args += [query, query]
            facts = [dict(r) for r in db.execute('SELECT scope,subject,name,value,event,updated,version FROM facts WHERE ' + fact_where + ' ORDER BY updated DESC LIMIT 100', args)] if 'facts' in tables else []
            pattern_args = [scope] if scope else []
            pattern_where = ' WHERE scope=?' if scope else ''
            patterns = []
            if 'social_patterns' in tables:
                for r in db.execute('SELECT scope,expression,situation,sources,weight,updated FROM social_patterns' + pattern_where + ' ORDER BY updated DESC LIMIT 100', pattern_args):
                    row = dict(r)
                    row['disabled'] = bool(db.execute('SELECT 1 FROM social_pattern_blocks WHERE scope=? AND expression=?', (r['scope'], r['expression'])).fetchone()) if 'social_pattern_blocks' in tables else False
                    row['expired'] = r['updated'] < time.time()-30*86400
                    patterns.append(row)
            pause_column='(SELECT until FROM initiative_pauses p WHERE p.scope=contacts.scope)' if 'initiative_pauses' in tables else 'NULL'
            contacts = [dict(r) for r in db.execute('SELECT scope,last_user,last_bot,last_attempt,unanswered,opted_out,'+pause_column+' AS paused_until FROM contacts' + pattern_where + ' ORDER BY last_user DESC LIMIT 100', pattern_args)] if 'contacts' in tables else []
            relations = [dict(r) for r in db.execute('SELECT scope,subject,affinity,emotion,expires,last_seen FROM relations' + (' WHERE '+' AND '.join(clauses) if clauses else '') + ' ORDER BY last_seen DESC LIMIT 100',([scope] if scope else [])+([subject] if subject else []))] if 'relations' in tables else []
            stats = {name: db.execute('SELECT count(*) FROM ' + name).fetchone()[0] for name in ['social_samples', 'social_patterns', 'social_learning'] if name in tables}
            epoch=db.execute('SELECT epoch FROM social_control WHERE id=1').fetchone()[0] if 'social_control' in tables else 0
            summaries=[]
            if 'memory_summaries' in tables and not subject:
                conditions=[];summary_args=[]
                if scope:conditions.append('scope=?');summary_args.append(scope)
                if query:conditions.append('instr(text,?)>0');summary_args.append(query)
                summaries=[dict(r) for r in db.execute('SELECT id,scope,text,updated FROM memory_summaries'+(' WHERE '+' AND '.join(conditions) if conditions else '')+' ORDER BY id DESC LIMIT 100',summary_args)]
            return {'initialized': True, 'epoch': epoch, 'facts': facts, 'summaries':summaries,'patterns': patterns, 'contacts': contacts, 'relations': relations, 'stats': stats, 'limit': 100, 'companion': {t:[dict(r) for r in db.execute('SELECT * FROM '+t+(' WHERE scope=?' if scope else '')+' ORDER BY rowid DESC LIMIT 30',([scope] if scope else []))] for t in ['companion_interests','companion_motives','companion_emotion'] if t in tables}}
        initialize_controls(db)
        db.execute('BEGIN IMMEDIATE')
        action, scope = data['action'], data.get('scope', '')
        if action in ('edit_fact', 'forget_fact'):
            key = (scope, data['subject'], data['name'])
            old = db.execute('SELECT version FROM facts WHERE scope=? AND subject=? AND name=? AND active=1', key).fetchone()
            if not old or old['version'] != data['expectedVersion']:
                return {'error': '记忆已发生变化，请刷新后重试', 'status': 409}
            from memory_reflection import invalidate
            invalidate(db,scope)
            db.execute('UPDATE facts SET active=0 WHERE scope=? AND subject=? AND name=?', key)
            if action == 'forget_fact':
                db.execute('INSERT OR IGNORE INTO memory_blocks VALUES(?,?,?)', key)
            else:
                version = db.execute('SELECT max(version)+1 FROM facts WHERE scope=? AND subject=? AND name=?', key).fetchone()[0]
                db.execute('INSERT INTO facts VALUES(?,?,?,?,?,?,?,1)', (*key, data['value'], 'platform-edit', time.time(), version))
                db.execute('DELETE FROM facts WHERE scope=? AND subject=? AND name=? AND version<?', (*key, max(1,version-9)))
                db.execute('DELETE FROM memory_blocks WHERE scope=? AND subject=? AND name=?', key)
                if 'fact_metadata' in tables:
                    db.execute("UPDATE fact_metadata SET origin='explicit' WHERE scope=? AND subject=? AND name=?", key)
        elif action == 'forget_summary':
            if 'memory_summaries' not in tables:return {'error':'摘要不存在','status':404}
            old=db.execute('SELECT updated FROM memory_summaries WHERE id=? AND scope=?',(data['id'],scope)).fetchone()
            if not old or old[0]!=data['expectedUpdated']:return {'error':'摘要已变化，请刷新后重试','status':409}
            db.execute('DELETE FROM memory_summaries WHERE id=? AND scope=?',(data['id'],scope))
            if 'memory_vectors' in tables:db.execute('DELETE FROM memory_vectors WHERE summary_id=?',(data['id'],))
            db.execute('DELETE FROM memory_rounds WHERE scope=?',(scope,))
            db.execute('UPDATE memory_reflection_control SET epoch=epoch+1 WHERE id=1')
        elif action == 'set_pattern':
            if db.execute('SELECT epoch FROM social_control WHERE id=1').fetchone()[0] != data['expectedEpoch']:
                return {'error': '学习管理状态已变化，请刷新后重试', 'status': 409}
            db.execute('UPDATE social_control SET epoch=epoch+1 WHERE id=1')
            key = (scope, data['expression'])
            old = db.execute('SELECT updated FROM social_patterns WHERE scope=? AND expression=?', key).fetchone()
            if not old or old[0] != data['expectedUpdated']:
                return {'error': '表达已发生变化，请刷新后重试', 'status': 409}
            if data['disabled']:
                db.execute('INSERT OR IGNORE INTO social_pattern_blocks VALUES(?,?)', key)
            else:
                db.execute('DELETE FROM social_pattern_blocks WHERE scope=? AND expression=?', key)
        elif action == 'clear_learning':
            # Global epoch is deliberately conservative: a clear invalidates any
            # in-flight learning batch in this Bot, without affecting other Bots.
            db.execute('UPDATE social_control SET epoch=epoch+1 WHERE id=1')
            db.execute('DELETE FROM social_pattern_blocks WHERE scope=?',(scope,))
            db.execute('DELETE FROM social_patterns WHERE scope=?', (scope,))
            db.execute('DELETE FROM social_samples WHERE scope=?', (scope,))
        elif action == 'reset_relation':
            old=db.execute('SELECT last_seen FROM relations WHERE scope=? AND subject=?',(scope,data['subject'])).fetchone()
            if not old or old[0]!=data['expectedLastSeen']:return {'error':'关系状态已变化，请刷新后重试','status':409}
            db.execute('DELETE FROM relations WHERE scope=? AND subject=?',(scope,data['subject']))
            if 'relation_progress' in tables:
                db.execute('DELETE FROM relation_progress WHERE scope=? AND subject=?',(scope,data['subject']))
        elif action == 'pause_contact':
            # Resuming an explicit recipient opt-out requires their own incoming
            # consent; platform users can pause but cannot override that consent.
            db.execute('UPDATE contacts SET opted_out=1 WHERE scope=?', (scope,))
        else:
            return {'error': '不支持的数据操作', 'status': 400}
        db.commit()
        return {'ok': True}
    finally:
        if db.in_transaction:db.rollback()
        db.row_factory=old_factory
        if connection is None:db.close()


if __name__ == '__main__':
    try:
        result = run(sys.argv[1], json.loads(sys.stdin.read(16384)))
    except sqlite3.OperationalError:
        result = {'error': '行为数据忙或尚未初始化，请稍后重试', 'status': 409}
    print(json.dumps(result, ensure_ascii=False))
