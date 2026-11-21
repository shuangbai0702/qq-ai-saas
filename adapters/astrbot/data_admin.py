"""Short-lived, local-only data access. Platform authenticates the Bot first.

No AstrBot import/startup, Provider access, remote URLs or arbitrary SQL input.
The only path is supplied by the control plane, never by the browser.
"""
import json
from pathlib import Path
import sqlite3
import sys
import time



def run(file, data, connection=None):
    # This entry point owns its transaction. Never commit (executescript also
    # commits) or roll back work borrowed from the runtime caller.
    if connection is not None and connection.in_transaction:
        return {'error': '行为数据事务正在处理中，请稍后重试', 'status': 409}
    file = Path(file)
    if connection is None and not file.exists() and data['action'] not in ('list_stickers','get_sticker','review_sticker'):
        if data['action'] != 'list':
            return {'error': '机器人尚未产生行为数据', 'status': 409}
        return {'persona_core_note':{'text':'','updated':0},'facts': [], 'patterns': [], 'contacts': [], 'relations': [], 'stats': {}, 'epoch': 0, 'initialized': False}
    if data['action'] in ('list_stickers','get_sticker','review_sticker') and not file.exists() and connection is None:
        return {'items':[],'pendingLimit':10,'approvedLimit':20} if data['action']=='list_stickers' else {'error':'表情包不存在','status':404}
    mode='ro' if data['action']=='list' else 'rw'
    db = connection or sqlite3.connect(file.as_uri() + '?mode='+mode, uri=True, timeout=3)
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
            page=max(0,min(10000,int(data.get('page',0))));totals={};page_sizes={}
            def listed(name,sql,args=(),size=100):
                import re
                base=re.split(r' ORDER BY ',sql,flags=re.I)[0]
                base=re.sub(r' LIMIT \d+$','',base,flags=re.I)
                totals[name]=db.execute('SELECT count(*) FROM ('+base+')',args).fetchone()[0];page_sizes[name]=size
                return db.execute(sql+' OFFSET ?',tuple(args)+(page*size,))

            clauses, args = [], []
            for key, value in [('scope', scope), ('subject', subject)]:
                if value:
                    clauses.append(key + '=?')
                    args.append(value)
            fact_where = ' AND '.join(['active=1'] + clauses)
            if query:
                fact_where += ' AND (instr(name,?)>0 OR instr(value,?)>0)'
                args += [query, query]
            facts = [dict(r) for r in listed('facts','SELECT scope,subject,name,value,event,updated,version FROM facts WHERE ' + fact_where + ' ORDER BY updated DESC LIMIT 100', args)] if 'facts' in tables else []
            pattern_args = [scope] if scope else []
            pattern_where = ' WHERE scope=?' if scope else ''
            patterns = []
            if 'social_patterns' in tables:
                for r in listed('patterns','SELECT scope,expression,situation,sources,weight,updated FROM social_patterns' + pattern_where + ' ORDER BY updated DESC LIMIT 100', pattern_args):
                    row = dict(r)
                    row['disabled'] = bool(db.execute('SELECT 1 FROM social_pattern_blocks WHERE scope=? AND expression=?', (r['scope'], r['expression'])).fetchone()) if 'social_pattern_blocks' in tables else False
                    row['expired'] = r['updated'] < time.time()-30*86400
                    from memory_products import version
                    row['version']=version(dict(r))
                    patterns.append(row)
            pause_column='(SELECT until FROM initiative_pauses p WHERE p.scope=contacts.scope)' if 'initiative_pauses' in tables else 'NULL'
            contacts = [dict(r) for r in listed('contacts','SELECT scope,last_user,last_bot,last_attempt,unanswered,opted_out,'+pause_column+' AS paused_until FROM contacts' + pattern_where + ' ORDER BY last_user DESC LIMIT 100', pattern_args)] if 'contacts' in tables else []
            relations = [dict(r) for r in listed('relations','SELECT * FROM relations' + (' WHERE '+' AND '.join(clauses) if clauses else '') + ' ORDER BY last_seen DESC LIMIT 100',([scope] if scope else [])+([subject] if subject else []))] if 'relations' in tables else []
            from relationships import user_version
            for relation in relations:
                relation['version']=user_version(relation)
                relation.pop('user_edit',None)
            stats = {name: db.execute('SELECT count(*) FROM ' + name).fetchone()[0] for name in ['social_samples', 'social_patterns', 'social_learning'] if name in tables}
            epoch=db.execute('SELECT epoch FROM social_control WHERE id=1').fetchone()[0] if 'social_control' in tables else 0
            summaries=[]
            if 'memory_summaries' in tables and not subject:
                conditions=[];summary_args=[]
                if scope:conditions.append('scope=?');summary_args.append(scope)
                if query:conditions.append('instr(text,?)>0');summary_args.append(query)
                summary_columns={r[1] for r in db.execute('PRAGMA table_info(memory_summaries)')}
                summary_fields='id,scope,text,updated'+(',schema_version' if 'schema_version' in summary_columns else '')+(',coalesce(json_array_length(claims),0) AS claim_count' if 'claims' in summary_columns else '')
                summaries=[dict(r) for r in listed('summaries','SELECT '+summary_fields+' FROM memory_summaries'+(' WHERE '+' AND '.join(conditions) if conditions else '')+' ORDER BY id DESC LIMIT 100',summary_args)]
            decisions=[dict(r) for r in listed('decisions','SELECT * FROM autonomy_decisions'+pattern_where+' ORDER BY at DESC LIMIT 50',pattern_args,50)] if 'autonomy_decisions' in tables else []
            pending=[dict(r) for r in listed('pending','SELECT scope,subject,event,question,answer,at FROM memory_pending'+(' WHERE '+' AND '.join(clauses) if clauses else '')+' ORDER BY at DESC LIMIT 100',([scope] if scope else [])+([subject] if subject else []))] if 'memory_pending' in tables else []
            core={'text':'','updated':0,'version':''}
            if 'memory_core_notes' in tables:
                from web_cognition import note,fingerprint
                current=note(db)
                core={'text':current['text'],'updated':current['updated'],'version':fingerprint(current)}
            companion={t:[dict(r) for r in listed(t,'SELECT * FROM '+t+(' WHERE scope=?' if scope else '')+' ORDER BY rowid DESC LIMIT 30',([scope] if scope else []),30)] for t in ['companion_interests','companion_motives','companion_emotion','companion_signals','dialogue_feedback'] if t in tables}
            from emotion_state import user_version as emotion_version
            for row in companion.get('companion_emotion',[]):
                row['version']=emotion_version(row);row.pop('user_edit',None)
            from companion_state import editable_version
            for kind,table in (('interest','companion_interests'),('motive','companion_motives')):
                for row in companion.get(table,[]):row['version']=editable_version(db,kind,{k:row[k] for k in (('scope','topic') if kind=='interest' else ('scope','subject','topic'))})
            from agreement_state import user_records,user_record_count
            agreements=user_records(db,scope,subject,page)
            totals['agreements']=user_record_count(db,scope,subject);page_sizes['agreements']=100
            return {'persona_core_note':core,'initialized': True, 'epoch': epoch, 'facts': facts, 'summaries':summaries,'patterns': patterns, 'contacts': contacts, 'decisions':decisions,'relations': relations, 'stats': stats, 'limit': 100,'pending':pending, 'companion':companion,'agreements':agreements,'pagination':{'page':page,'totals':totals,'pages':max([1]+[(totals[k]+page_sizes[k]-1)//page_sizes[k] for k in totals])}}
        if data['action'] in ('edit_fact','forget_fact'):
            from memory_scope import initialize_controls
            initialize_controls(db)
        elif data['action'] in ('set_pattern','edit_pattern','clear_learning'):
            from social import initialize_controls
            initialize_controls(db)
        elif data['action']=='edit_agreement':
            from agreement_state import initialize
            initialize(db)
        db.execute('BEGIN IMMEDIATE')
        action, scope = data['action'], data.get('scope', '')
        if action == 'edit_core_note':
            from web_cognition import edit
            result=edit(db,data)
            if result.get('ok'):db.commit()
            return result
        elif action in ('edit_fact', 'forget_fact'):
            from hosted_memory import edit_by_user
            result=edit_by_user(db,data)
            if not result.get('ok'):return result
        elif action == 'forget_summary':
            from memory_reflection import forget_summary_by_user
            result=forget_summary_by_user(db,data)
            if not result.get('ok'):return result
        elif action in ('set_pattern','edit_pattern','clear_learning'):
            from social import edit_learning_by_user
            result=edit_learning_by_user(db,data)
            if not result.get('ok'):return result
        elif action == 'edit_companion':
            from companion_state import edit_by_user
            result=edit_by_user(db,data)
            if not result.get('ok'):return result
            db.commit();return result
        elif action == 'edit_emotion':
            from emotion_state import edit_by_user
            result=edit_by_user(db,data)
            if not result.get('ok'):return result
            db.commit();return result
        elif action == 'edit_agreement':
            from agreement_state import edit_by_user
            result=edit_by_user(db,data)
            if not result.get('ok'):return result
            db.commit();return result
        elif action == 'edit_relation':
            from relationships import edit_by_user
            result=edit_by_user(db,data)
            if not result.get('ok'):return result
            db.commit();return result
        elif action == 'reset_relation':
            from relationships import reset_by_user
            result=reset_by_user(db,data)
            if not result.get('ok'):return result
        elif action == 'pause_contact':
            # Explicit operator controls remain hard fences. Conversation text
            # cannot grant or revoke platform permission.
            db.execute('UPDATE contacts SET opted_out=1 WHERE scope=?', (scope,))
            if 'autonomy_decisions' in tables:
                at=time.time();db.execute("INSERT INTO autonomy_decisions(scope,event,at,kind,reply,extra_intervals,base_seconds,cooldown_until,reason) VALUES(?,?,?,'operator',0,0,0,0,'manual_pause')",(scope,'operator:'+str(at),at))
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
