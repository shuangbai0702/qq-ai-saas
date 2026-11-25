"""Scoped, bounded recall of attributed facts.

Mechanisms informed by dsh-fish-memory (stable facts vs decaying events),
LivingMemory (CJK retrieval/scope separation), QQ-agent (per-member records).
This is an independent SQLite implementation, not those frameworks' plugin API.
Optional bounded semantic recall uses only active, attributed facts.
"""
import asyncio,json,hashlib,math,re,collections,time
from memory_policy import STOP_WORDS as STOP,tokens,FACT_NAME_CHARS,FACT_VALUE_CHARS

ALIASES={'生日':('生日','生辰','出生日期'), '称呼':('名字','称呼','怎么叫','叫什么'),
         '饮料偏好':('喝什么','喝的','饮料','爱喝'), '食物偏好':('吃什么','爱吃','食物'),
         '居住城市':('住哪','住在','城市'), '职业':('工作','职业')}


from memory_scope import initialize_controls
from memory_scope import person_filter,fact_rows,fact_item,shared_person,blocked_facts,record_correction,release_value,value_hash

def retire_legacy_results(db,cutoff):
    """Remove obsolete research-task copies, never current person cognition.

    These origins are excluded by every fact reader and have no writer in the
    current architecture. Their audit/rollback copies are not a memory product.
    The caller owns a committed semantic retirement transaction.
    """
    rows=db.execute("SELECT f.scope,f.subject,f.name FROM facts f JOIN fact_metadata m ON m.scope=f.scope AND m.subject=f.subject AND m.name=f.name WHERE f.scope='self' AND f.subject='0' AND m.origin='autonomous_result' AND f.updated<=?",(cutoff,)).fetchall()
    for key in rows:
        db.execute('DELETE FROM fact_metadata WHERE scope=? AND subject=? AND name=?',key)
        db.execute('DELETE FROM facts WHERE scope=? AND subject=? AND name=?',key)
    return len(rows)


def initialize_metadata(db):
    db.execute('CREATE TABLE IF NOT EXISTS fact_metadata(scope TEXT,subject TEXT,name TEXT,kind TEXT,origin TEXT,importance REAL,last_recalled REAL DEFAULT 0,recalls INTEGER DEFAULT 0,PRIMARY KEY(scope,subject,name))')

class ScopedMemory:
    def __init__(self,db):
        self.db=db;self.closed=False;self.embedding_lock=asyncio.Lock()
        initialize_controls(db)
        initialize_metadata(db)
        db.executescript('''CREATE TABLE IF NOT EXISTS facts(scope TEXT,subject TEXT,name TEXT,value TEXT,event TEXT,updated REAL,version INTEGER,active INTEGER,PRIMARY KEY(scope,subject,name,version));
        CREATE INDEX IF NOT EXISTS facts_active_person ON facts(scope,subject,active);
        CREATE INDEX IF NOT EXISTS facts_shared_person ON facts(subject,active,updated);
        CREATE TABLE IF NOT EXISTS memory_capture_budget(scope TEXT,subject TEXT,day INTEGER,count INTEGER,PRIMARY KEY(scope,subject,day));
        DROP TRIGGER IF EXISTS fact_vectors_insert;
        DROP TRIGGER IF EXISTS fact_vectors_update;
        DROP TRIGGER IF EXISTS fact_vectors_delete;
        DROP TABLE IF EXISTS fact_vectors;
        DROP TABLE IF EXISTS fact_vector_control;''')

    def remember(self,scope,subject,name,value,event,now,origin='explicit',kind='stable'):
        where,args=person_filter(scope,subject)
        # Every automatic origin obeys the same block judgment: whole-name
        # forgets refuse the field, correction records refuse their exact old
        # values. Explicit records may restore only the value they name.
        if origin!='explicit':
            hard,rejected,blocked_values=blocked_facts(self.db,scope,subject)
            if name in hard:return '已遗忘的资料不会自动重新保存'
            if value_hash(value) in blocked_values:return '已纠正的旧值不会自动重新保存'
        if event and self.db.execute('SELECT 1 FROM facts WHERE scope=? AND subject=? AND name=? AND event=?',(scope,subject,name,str(event))).fetchone():return '重复消息未再次更新记忆'
        current=next((r for r in fact_rows(self.db,scope,subject) if r[0]==name),None)
        # Natural chat cannot silently overwrite a deliberately recorded fact.
        if origin=='self_report' and current and current[7]=='explicit':return '已有明确记忆，自动资料未覆盖；可用更正记忆更新'
        if current and current[1]==value:return '已有相同长期事实，未重复保存'
        total=self.db.execute('SELECT count(*) FROM facts').fetchone()[0]
        person=self.db.execute('SELECT count(DISTINCT name) FROM facts WHERE '+where+' AND active=1',args).fetchone()[0]
        if total>=20000 or (not current and person>=200):return '长期事实额度已满，未保存'
        if origin=='self_report':
            budget_scope='person:'+subject if shared_person(scope,subject) else scope
            day=int(now//86400)
            if shared_person(scope,subject):
                row=self.db.execute('SELECT coalesce(sum(count),0) FROM memory_capture_budget WHERE subject=? AND day=? AND (scope=? OR scope=? OR scope LIKE \'group:%\')',(subject,day,budget_scope,'private:'+subject)).fetchone()
            else:row=self.db.execute('SELECT count FROM memory_capture_budget WHERE scope=? AND subject=? AND day=?',(budget_scope,subject,day)).fetchone()
            if row and row[0]>=20:return '今日自动资料更新额度已满，未保存'
            self.db.execute('INSERT INTO memory_capture_budget VALUES(?,?,?,1) ON CONFLICT(scope,subject,day) DO UPDATE SET count=count+1',(budget_scope,subject,day))
            self.db.execute('DELETE FROM memory_capture_budget WHERE day<?',(day-7,))
        version=self.db.execute('SELECT coalesce(max(version),0)+1 FROM facts WHERE '+where+' AND name=?',args+(name,)).fetchone()[0]
        self.db.execute('UPDATE facts SET active=0 WHERE '+where+' AND name=?',args+(name,))
        self.db.execute('INSERT INTO facts VALUES(?,?,?,?,?,?,?,1)',(scope,subject,name,value,str(event),now,version))
        # A rejected write (quota, duplicate, etc.) must not lift a forget block.
        if origin=='explicit':release_value(self.db,scope,subject,name,value)
        self.db.execute('DELETE FROM facts WHERE '+where+' AND name=? AND version<?',args+(name,max(1,version-9)))
        importance=1.0 if name in ('称呼','生日') else 0.8 if kind=='stable' else 0.5
        self.db.execute('INSERT INTO fact_metadata(scope,subject,name,kind,origin,importance) VALUES(?,?,?,?,?,?) ON CONFLICT(scope,subject,name) DO UPDATE SET kind=excluded.kind,origin=excluded.origin,importance=excluded.importance,last_recalled=0,recalls=0',(scope,subject,name,kind,origin,importance))
        return '长期事实已成功保存/更新' if origin=='explicit' else '本人明确自述已自动保存/更新'

    def observe(self,scope,subject,text,event,now):
        text=text.strip();status='没有新增长期事实'
        m=re.fullmatch(r'(记住|更正记忆|记住事件)[：:]\s*([^=＝\n]{1,40})[=＝](.{1,300})',text)
        if m:
            if m[1]=='更正记忆':
                from memory_invalidation import invalidate
                invalidate(self.db,scope,subject)
                # Record what this explicit correction replaced so a later
                # asynchronous extraction cannot quietly write the old value.
                previous=next((r for r in fact_rows(self.db,scope,subject) if r[0]==m[2].strip()),None)
            else:previous=None
            status=self.remember(scope,subject,m[2].strip(),m[3].strip(),event,now,kind='event' if m[1]=='记住事件' else 'stable')
            if previous is not None and previous[1]!=m[3].strip() and '成功保存' in status:
                record_correction(self.db,scope,subject,m[2].strip(),previous[1],event,quote=text,now=now)
        else:
            forget=re.fullmatch(r'忘记[：:]\s*(.{1,40})',text)
            if forget:
                from memory_invalidation import invalidate
                from memory_scope import block_all_fact
                invalidate(self.db,scope,subject)
                block_all_fact(self.db,scope,subject,forget[1].strip())
                where,args=person_filter(scope,subject)
                self.db.execute('UPDATE facts SET active=0 WHERE '+where+' AND name=?',args+(forget[1].strip(),));status='该事实已标记遗忘'
            # Automatic regex self-report extraction is retired (D package): the
            # summary model's evidence/claims pipeline is the semantic writer.
            # Explicit 记住/忘记/更正 commands remain the operator interface.
        return status

    def correct(self,scope,subject,name,old_value,new_value,event,quote,now):
        """Apply one evidence-checked correction inside the caller's transaction.

        The previous version is retired, an optional replacement is written,
        and the correction evidence plus revision commit together. Ordinary
        corrections never trigger the hard-forget epoch; a value whose hash is
        already rejected cannot be reintroduced by a late extraction.
        """
        where,args=person_filter(scope,subject)
        row=self.db.execute('SELECT value FROM facts WHERE '+where+' AND name=? AND active=1 ORDER BY version DESC LIMIT 1',args+(name,)).fetchone()
        if not row or row[0]!=old_value:return '未找到与纠正匹配的当前资料'
        if event and self.db.execute('SELECT 1 FROM facts WHERE scope=? AND subject=? AND name=? AND event=?',(scope,subject,name,str(event))).fetchone():return '重复消息未再次更新记忆'
        if new_value:
            hard,rejected,blocked_values=blocked_facts(self.db,scope,subject)
            if name in hard or value_hash(new_value) in blocked_values:return '该资料已被本人遗忘或纠正，不能再次写入'
        self.db.execute('UPDATE facts SET active=0 WHERE '+where+' AND name=? AND active=1',args+(name,))
        if new_value:
            version=self.db.execute('SELECT coalesce(max(version),0)+1 FROM facts WHERE '+where+' AND name=?',args+(name,)).fetchone()[0]
            self.db.execute('INSERT INTO facts VALUES(?,?,?,?,?,?,?,1)',(scope,subject,name,new_value,str(event),now,version))
            self.db.execute('DELETE FROM facts WHERE '+where+' AND name=? AND version<?',args+(name,max(1,version-9)))
            importance=1.0 if name in ('称呼','生日') else 0.8
            self.db.execute("INSERT INTO fact_metadata(scope,subject,name,kind,origin,importance) VALUES(?,?,?,'stable','explicit',?) ON CONFLICT(scope,subject,name) DO UPDATE SET origin='explicit',importance=excluded.importance,last_recalled=0,recalls=0",(scope,subject,name,importance))
        record_correction(self.db,scope,subject,name,old_value,event,quote,now)
        from memory_operation_audit import forget_fact
        forget_fact(self.db,subject,name)
        return '已按本人证据纠正资料'

    def recall(self,scope,subject,query,now,limit=8,budget=1600):
        rows=fact_rows(self.db,scope,subject)
        q=tokens(query);docs=[tokens(r[0]+' '+r[1])for r in rows];df=collections.Counter(t for d in docs for t in d)
        scores=[]
        for r,doc in zip(rows,docs):
            overlap=q&doc;evidence=sum(math.log(1+(len(rows)+1)/(df[t]+1))for t in overlap)
            if len(r[0])>=2 and r[0] in query:evidence+=4
            if any(cue in query for cue in ALIASES.get(r[0],())):evidence+=4
            from memory_strength import fact_strength
            activation=fact_strength(r[5],r[4],now,r[6])
            # Relevance admits a memory; freshness only ranks it. Old relevant
            # events can still be recalled, unrelated fresh events get no boost.
            score=evidence*(.6+.25*activation+.15*r[6])
            scores.append((r,score,activation))
        relevant=sorted((x for x in scores if x[1]>0),key=lambda x:(-x[1],-x[0][4]))
        anchors=sorted((x for x in scores if x[0][5]=='stable' and x[0][0] in ('称呼','生日')),key=lambda x:-x[0][6])[:2]
        fallback=sorted((x for x in scores if x[0][5]=='stable'),key=lambda x:-x[0][4])
        selected=[];seen=set();used=0
        for r,score,activation in relevant+anchors+fallback:
            if r[0] in seen:continue
            size=len(r[0])+len(r[1])
            if used+size>budget:continue
            selected.append(fact_item(r,subject,'topic_match' if score>0 else 'profile' if r[0] in ('称呼','生日') else 'recent_fact'))
            seen.add(r[0]);used+=size
            if len(selected)>=limit:break
        return selected

    def close(self):self.closed=True


def edit_by_user(db,data):
    """CAS edit of fact state; the authenticated admin caller owns the transaction."""
    if not data.get('actorId'):return {'error':'缺少真实用户操作来源','status':403}
    scope=data['scope'];action=data['action']
    tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    key = (scope, data['subject'], data['name'])
    old = db.execute('SELECT version FROM facts WHERE scope=? AND subject=? AND name=? AND active=1', key).fetchone()
    if not old or old['version'] != data['expectedVersion']:
        return {'error': '记忆已发生变化，请刷新后重试', 'status': 409}
    from memory_scope import person_filter
    # Editing is not forgetting. Keep Core Note, summaries, pending
    # evidence and lawful checkpoints intact. Compression compares
    # its cognition snapshot again inside the commit transaction.
    where,args=person_filter(scope,data['subject'])
    initialize_metadata(db);tables.add('fact_metadata')
    origins=[fact_fingerprint(fact_item(row,data['subject'],'checkpoint_foundation'))
             for row in fact_rows(db,scope,data['subject']) if row[0]==data['name']]
    proof_column='user_edit' in {r[1] for r in db.execute('PRAGMA table_info(fact_metadata)')}
    previous=db.execute('SELECT user_edit FROM fact_metadata WHERE scope=? AND subject=? AND name=?',key).fetchone() if proof_column else None
    proof=json.loads(previous[0]) if previous and previous[0] else {}
    version=db.execute('SELECT max(version)+1 FROM facts WHERE '+where+' AND name=?',args+(data['name'],)).fetchone()[0]
    db.execute('UPDATE facts SET active=0 WHERE '+where+' AND name=?',args+(data['name'],))
    if action == 'forget_fact':
        from memory_operation_audit import forget_fact
        from memory_scope import block_all_fact
        forget_fact(db,data['subject'],data['name'])
        block_all_fact(db,*key)
        db.execute("INSERT INTO fact_metadata(scope,subject,name,kind,origin,importance) VALUES(?,?,?,'stable','explicit',.8) ON CONFLICT(scope,subject,name) DO NOTHING",key)
        if not proof_column:db.execute('ALTER TABLE fact_metadata ADD COLUMN user_edit TEXT')
        proof={'actorId':data['actorId'],'target':None,'origins':list(dict.fromkeys([*proof.get('origins',[]),*origins]))}
        db.execute('UPDATE fact_metadata SET user_edit=? WHERE scope=? AND subject=? AND name=?',(json.dumps(proof,separators=(',',':')),*key))
    else:
        db.execute('INSERT INTO facts VALUES(?,?,?,?,?,?,?,1)', (*key, data['value'], 'platform-edit', time.time(), version))
        db.execute('DELETE FROM facts WHERE '+where+' AND name=? AND version<?',args+(data['name'],max(1,version-9)))
        # Authenticated platform edit: lift this value's block only;
        # other correction/forget records of the field stay recorded.
        from memory_scope import release_value
        release_value(db,scope,data['subject'],data['name'],data['value'])
        if 'fact_metadata' in tables:
            db.execute("INSERT INTO fact_metadata(scope,subject,name,kind,origin,importance) VALUES(?,?,?,'stable','explicit',.8) ON CONFLICT(scope,subject,name) DO UPDATE SET origin='explicit'",key)
            if not proof_column:db.execute('ALTER TABLE fact_metadata ADD COLUMN user_edit TEXT')
            current=next(fact_item(row,data['subject'],'checkpoint_foundation') for row in fact_rows(db,scope,data['subject']) if row[0]==data['name'])
            proof={'actorId':data['actorId'],'target':fact_fingerprint(current),'origins':list(dict.fromkeys([*proof.get('origins',[]),*origins]))}
            db.execute('UPDATE fact_metadata SET user_edit=? WHERE scope=? AND subject=? AND name=?',(json.dumps(proof,separators=(',',':')),*key))
    return {'ok':True}


def fact_fingerprint(item):
    """Only this fact's identity/version, never an unrelated memory snapshot."""
    if item is None:return None
    return hashlib.sha256(json.dumps([item.get(k) for k in ('source_scope','subject','name','value','source_event','version')],ensure_ascii=False,separators=(',',':')).encode()).hexdigest()


def user_edits(db):
    if 'user_edit' not in {r[1] for r in db.execute('PRAGMA table_info(fact_metadata)')}:return []
    result=[]
    for scope,person,name,raw in db.execute('SELECT scope,subject,name,user_edit FROM fact_metadata WHERE user_edit IS NOT NULL'):
        proof=json.loads(raw)
        current=next((fact_item(row,person,'checkpoint_foundation') for row in fact_rows(db,scope,person) if row[0]==name),None)
        # A lawful later compression may replace the user version. Its own
        # transaction owns the new basis; old user authority cannot edit it.
        if proof.get('actorId') and fact_fingerprint(current)==proof.get('target'):
            result.append({'proof':proof,'fact':current})
    return result


def project_user_edits(snapshot,edits):
    """Replace exact user-authorized foundation facts, never dynamic packets."""
    if snapshot is None or not edits:return snapshot
    import copy
    from context_protocol import FOUNDATION_CURRENT,ContextPrefixViolation
    from memory_model_view import expand,memory_json
    changes={}
    for change in edits:
        proof=change['proof'];current=change['fact']
        if not proof.get('actorId') or fact_fingerprint(current)!=proof.get('target'):raise ContextPrefixViolation('web_fact_provenance_mismatch')
        for origin in proof.get('origins',[]):changes[origin]=current
    def facts(rows):
        result=[]
        for row in rows:
            identity=fact_fingerprint(row)
            if identity in changes and changes[identity] is None:continue
            replacement=changes.get(identity)
            result.append({**replacement,'recall_reason':row['recall_reason']} if replacement and 'recall_reason' in row else copy.deepcopy(replacement or row))
        return result
    def patch(messages):
        result=copy.deepcopy(messages)
        for message in result:
            content=message.get('content')
            if message.get('role')!='user' or not isinstance(content,str) or not content.startswith(FOUNDATION_CURRENT+'\n'):continue
            packet=expand(json.loads(content.split('\n',1)[1]))
            if packet.get('region')!='long_term_facts' or not isinstance(packet.get('facts'),list):continue
            updated=facts(packet['facts'])
            if updated!=packet['facts']:
                packet['facts']=updated;message['content']=FOUNDATION_CURRENT+'\n'+memory_json(packet)
        return result
    result=copy.copy(snapshot);result.state=copy.deepcopy(snapshot.state)
    foundation=result.state.get('memory_foundation',{})
    if isinstance(foundation.get('facts'),list):foundation['facts']=facts(foundation['facts'])
    result.base=patch(snapshot.base);result.wire=patch(snapshot.wire)
    return result


FACT_NAME_CHARS=40
FACT_VALUE_CHARS=200
FACT_FIELDS=('scope','subject','name','value','event','updated','version','active','kind','importance','origin')

def semantic_fact_state(db,scope,person):
    from memory_scope import blocked_facts,value_hash
    where,args=person_filter(scope,person,'f')
    hard,_,blocked=blocked_facts(db,scope,person)
    rows=db.execute('''SELECT f.scope,f.subject,f.name,f.value,f.event,f.updated,f.version,f.active,
      coalesce(m.kind,'stable'),coalesce(m.importance,.8),coalesce(m.origin,'explicit')
      FROM facts f LEFT JOIN fact_metadata m ON f.scope=m.scope AND f.subject=m.subject AND f.name=m.name
      WHERE '''+where+''' AND f.active=1 AND coalesce(m.origin,'explicit')<>'autonomous_result'
      ORDER BY f.scope,f.subject,f.name,f.version''',args).fetchall()
    return [dict(zip(FACT_FIELDS,row)) for row in rows if row[2] not in hard and value_hash(row[3]) not in blocked]


def apply_summary_facts(db,scope,cfg,data,memory_state,people,event,now,audit=None):
    """Fact-owned semantic CAS updates; caller supplies the legal summary transaction."""
    from memory_scope import memory_version,safe_memory_text as _safe_memory_text
    def reject(category,index,reason):
        if audit:audit.mark(category,index,'rejected',reason)
    saved=0;claimed=set()
    operations=data.get('fact_operations',[])
    if cfg.get('memory',True) and cfg.get('memoryAutoCapture') and isinstance(operations,list):
        for index,item in enumerate(operations):
            if not isinstance(item,dict):reject('facts',index,'invalid_item');continue
            action={'create':'add','replace':'revise','delete':'forget'}.get(item.get('op'),item.get('op'));person=item.get('subject');target_scope=item.get('scope');name=item.get('name');expected=item.get('expected')
            if 'expected_version' in item:
                offered=[r for r in memory_state.get('facts',[]) if r['subject']==person and r['name']==name]
                if item['expected_version']!=(memory_version(offered) if offered else None):reject('facts',index,'expected_mismatch');continue
                expected=offered
            if not isinstance(person,str) or person not in people or target_scope!=scope or action not in ('add','revise','merge','weaken','forget'):reject('facts',index,'invalid_target_or_operation');continue
            if not _safe_memory_text(name,FACT_NAME_CHARS) or not isinstance(expected,list) or any(not isinstance(x,dict) for x in expected):reject('facts',index,'invalid_field_or_expected');continue
            if action=='add' and expected or action!='add' and not expected:reject('facts',index,'expected_presence');continue
            if any(x not in memory_state.get('facts',[]) or x.get('subject')!=person for x in expected):reject('facts',index,'expected_mismatch');continue
            names={x['name'] for x in expected}|{name}
            if any(not _safe_memory_text(n,FACT_NAME_CHARS) or re.search(r'(?i)身份|\b(?:owner|admin|role|policy|system|binding|uin|sender|platform)\b',n) for n in names):reject('facts',index,'unsafe_field');continue
            if action!='merge' and names!={name}:reject('facts',index,'unexpected_fields');continue
            keys={(person,n) for n in names}
            if claimed & keys:reject('facts',index,'duplicate_target');continue
            # All active copies of each affected name must match the offered
            # snapshot. This also catches a higher-priority legacy copy or ABA.
            current=[x for x in semantic_fact_state(db,scope,person) if x['name'] in names]
            offered=[x for x in memory_state.get('facts',[]) if x['subject']==person and x['name'] in names]
            if current!=offered:
                reject('facts',index,'snapshot_changed');raise ValueError('semantic_fact_snapshot_changed')
            if len(expected)!=len(current) or any(x not in expected for x in current):reject('facts',index,'incomplete_expected');continue
            value=item.get('value');kind=item.get('kind','stable');importance=item.get('importance',.8)
            if action=='forget':value=None
            elif action=='weaken':
                if len({x['value'] for x in expected})!=1:reject('facts',index,'ambiguous_weaken');continue
                value=expected[0]['value'];kind=item.get('kind',expected[0]['kind'])
                if 'importance' not in item:reject('facts',index,'missing_importance');continue
            if action!='forget' and value is None:reject('facts',index,'missing_value');continue
            if value is not None and (not _safe_memory_text(value,FACT_VALUE_CHARS) or kind not in ('stable','event','state')):reject('facts',index,'unsafe_value_or_kind');continue
            if type(importance) not in (int,float) or not math.isfinite(importance) or not 0<=importance<=1:reject('facts',index,'invalid_importance');continue
            if action=='weaken' and any(importance>=x['importance'] for x in expected):reject('facts',index,'not_weakened');continue
            from memory_scope import blocked_facts,value_hash
            hard,_,blocked=blocked_facts(db,scope,person)
            if name in hard or value is not None and value_hash(value) in blocked:reject('facts',index,'human_memory_block');continue
            if value is not None and db.execute('SELECT count(*) FROM facts').fetchone()[0]>=20000:
                reject('facts',index,'storage_capacity');continue
            where,args=person_filter(scope,person)
            version=db.execute('SELECT coalesce(max(version),0)+1 FROM facts WHERE '+where+' AND name=?',args+(name,)).fetchone()[0]
            for old in expected:
                db.execute('UPDATE facts SET active=0 WHERE scope=? AND subject=? AND name=? AND version=? AND active=1',
                           (old['scope'],person,old['name'],old['version']))
            if item.get('op')=='delete':
                db.execute('DELETE FROM facts WHERE '+where+' AND name=?',args+(name,))
                db.execute('DELETE FROM fact_metadata WHERE '+where+' AND name=?',args+(name,))
            if value is not None:
                db.execute('INSERT INTO facts VALUES(?,?,?,?,?,?,?,1)',(scope,person,name,value,event,now,version))
                db.execute('''INSERT INTO fact_metadata(scope,subject,name,kind,origin,importance) VALUES(?,?,?,?,?,?)
                  ON CONFLICT(scope,subject,name) DO UPDATE SET kind=excluded.kind,origin=excluded.origin,
                  importance=excluded.importance,last_recalled=0,recalls=0''',(scope,person,name,kind,'semantic',importance))
            claimed.update(keys);saved+=1
            if audit:audit.mark('facts',index,'accepted','applied',True)
    elif isinstance(operations,list):
        for index in range(len(operations)):reject('facts',index,'disabled')
    return saved


def apply_summary_corrections(db,memory,scope,items,evidence,before,now,on_unverified=None):
    """Exact author corrections; general semantic edits use apply_summary_facts."""
    saved=0
    # Natural corrections: only the event's real author correcting their own
    # data, against the stored current value. Third-person, quoted or
    # otherwise ambiguous cases go to unverified signals, never to facts.
    for item in items:
        source=evidence(item,'user')
        if not source:continue
        quote=item.get('quote');name,old,new=item.get('name'),item.get('oldValue'),item.get('newValue')
        if not isinstance(quote,str) or not isinstance(name,str) or not isinstance(old,str) or not 1<=len(name)<=40 or not 1<=len(old)<=200:continue
        if new is not None and (not isinstance(new,str) or not 1<=len(new)<=200 or new==old):continue
        person=source['sender_id']
        current=next((r for r in fact_rows(db,scope,person) if r[0]==name),None)
        # Extraction started against this exact version. Returning late
        # after another correction (including A -> B -> A) is not consent
        # to overwrite the newer fact, even when its value matches again.
        if current and (current[10],current[3],current[2])!=before['facts'].get((person,name)):continue
        # The quote must be the author's own first-person statement with an
        # explicit correction relation; anything else waits for review.
        explicit_relation=old in quote and re.search(r'不(?:是|叫|姓|对|准确)|别再|不要再|更正|纠正|说错|记错|改(?:叫|成|为)|以后叫我',quote)
        verified=bool(current) and current[1]==old and explicit_relation and (new is None or new in quote) and re.search(r'我|本人',quote) and not re.search(r'[“”"「」]|假设|如果|比如|他说|她说|他们说|别人|有人|大家|摘要|道歉|抱歉|[他她它][^，。；;！!]{0,8}(?:叫|是|说)',quote+name)
        if not verified:
            if on_unverified:on_unverified(source,{'name':name,'oldValue':old,'newValue':new,'quote':quote[:120]})
            continue
        saved+=int('纠正' in memory.correct(scope,person,name,old,new,source['event'],quote,now))
    return saved
