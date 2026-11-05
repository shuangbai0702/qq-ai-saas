"""Scoped, bounded lexical recall and conservative first-person profile capture.

Mechanisms informed by dsh-fish-memory (stable facts vs decaying events),
LivingMemory (CJK retrieval/scope separation), QQ-agent (per-member records).
This is an independent SQLite implementation, not those frameworks' plugin API.
Optional bounded semantic recall uses only active, attributed facts.
"""
import asyncio,json,hashlib,math,re,collections

STOP={'什么','么了','是什','我的','你的','记得','记住','一下','这个','那个','你好','知道','告诉','最近','现在','还是','之前','我们','可以','是不是','有什'}
ALIASES={'生日':('生日','生辰','出生日期'), '称呼':('名字','称呼','怎么叫','叫什么'),
         '饮料偏好':('喝什么','喝的','饮料','爱喝'), '食物偏好':('吃什么','爱吃','食物'),
         '居住城市':('住哪','住在','城市'), '职业':('工作','职业')}

def tokens(text):
    result=set(re.findall(r'[a-z0-9_]{2,}',text.lower()))
    for run in re.findall(r'[\u4e00-\u9fff]+',text):
        result.update(run[i:i+2] for i in range(len(run)-1))
    return result-STOP

def self_report(text):
    """Only full, short, unquoted self-declarations. Ambiguous prose is ignored."""
    text=text.strip().rstrip('。！!')
    if not text or len(text)>100 or re.search(r'''[\n?？“”"'「」『』<>]|什么|哪里|哪儿|谁|多少|怎么|假如|如果|假设|比如|例如|开玩笑|骗你|转述|其实不是|别人|他说|她说|或者|还是|可能|大概|好像|听说|据说|假装|扮演|以前|曾经|[吗吧呢]$''',text):return None
    specs=[
        ('称呼',r'(?:我叫|你可以叫我|叫我)([^，,。；;！!]{1,20}?)(?:就好|就行)?'),
        ('生日',r'我的生日是((?:[0-9零一二三四五六七八九十]{2,4}年)?[0-9零一二三四五六七八九十]{1,3}月[0-9零一二三四五六七八九十]{1,3}[日号])'),
        ('居住城市',r'我(?:现在)?住在([^，,。；;！!]{2,20})'),
        ('职业',r'我的职业是([^，,。；;！!]{1,20})'),
        ('饮料偏好',r'我(?:现在|最近)?(?:最)?(?:喜欢喝|爱喝|都喝)([^，,。；;！!]{1,30})'),
        ('食物偏好',r'我(?:现在|最近)?(?:最)?(?:喜欢吃|爱吃)([^，,。；;！!]{1,30})'),
    ]
    for name,pattern in specs:
        m=re.fullmatch(pattern,text)
        if m and not re.search(r'不|没|别|不要|主人|管理员|指令|提示词|系统',m[1]) and not (name=='称呼' and m[1].startswith('你')):return name,m[1].strip()
    return None

from data_admin import initialize_controls
from memory_scope import person_filter,fact_rows,fact_item,shared_person

class ScopedMemory:
    def __init__(self,db):
        self.db=db;self.closed=False;self.embedding_lock=asyncio.Lock()
        initialize_controls(db)
        db.executescript('''CREATE INDEX IF NOT EXISTS facts_active_person ON facts(scope,subject,active);
        CREATE INDEX IF NOT EXISTS facts_shared_person ON facts(subject,active,updated);
        CREATE TABLE IF NOT EXISTS fact_metadata(scope TEXT,subject TEXT,name TEXT,kind TEXT,origin TEXT,importance REAL,last_recalled REAL DEFAULT 0,recalls INTEGER DEFAULT 0,PRIMARY KEY(scope,subject,name));
        CREATE TABLE IF NOT EXISTS memory_capture_budget(scope TEXT,subject TEXT,day INTEGER,count INTEGER,PRIMARY KEY(scope,subject,day));
        CREATE TABLE IF NOT EXISTS memory_embedding_budget(day INTEGER PRIMARY KEY,count INTEGER);
        CREATE TABLE IF NOT EXISTS fact_vector_control(id INTEGER PRIMARY KEY CHECK(id=1),epoch INTEGER NOT NULL);
        INSERT OR IGNORE INTO fact_vector_control VALUES(1,0);
        CREATE TABLE IF NOT EXISTS fact_vectors(scope TEXT,subject TEXT,name TEXT,version INTEGER,model TEXT,fingerprint TEXT,vector TEXT,PRIMARY KEY(scope,subject,name));
        CREATE TRIGGER IF NOT EXISTS fact_vectors_insert AFTER INSERT ON facts BEGIN
          UPDATE fact_vector_control SET epoch=epoch+1 WHERE id=1;
          DELETE FROM fact_vectors WHERE scope=NEW.scope AND subject=NEW.subject AND name=NEW.name;
        END;
        CREATE TRIGGER IF NOT EXISTS fact_vectors_update AFTER UPDATE ON facts BEGIN
          UPDATE fact_vector_control SET epoch=epoch+1 WHERE id=1;
          DELETE FROM fact_vectors WHERE (scope=OLD.scope AND subject=OLD.subject AND name=OLD.name) OR (scope=NEW.scope AND subject=NEW.subject AND name=NEW.name);
        END;
        CREATE TRIGGER IF NOT EXISTS fact_vectors_delete AFTER DELETE ON facts BEGIN
          UPDATE fact_vector_control SET epoch=epoch+1 WHERE id=1;
          DELETE FROM fact_vectors WHERE scope=OLD.scope AND subject=OLD.subject AND name=OLD.name;
        END;''')

    def remember(self,scope,subject,name,value,event,now,origin='explicit',kind='stable'):
        where,args=person_filter(scope,subject)
        if origin=='self_report' and self.db.execute('SELECT 1 FROM memory_blocks WHERE '+where+' AND name=?',args+(name,)).fetchone():return '已遗忘的资料不会自动重新保存'
        if event and self.db.execute('SELECT 1 FROM facts WHERE scope=? AND subject=? AND name=? AND event=?',(scope,subject,name,str(event))).fetchone():return '重复消息未再次更新记忆'
        if origin=='explicit':self.db.execute('DELETE FROM memory_blocks WHERE '+where+' AND name=?',args+(name,))
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
        self.db.execute('DELETE FROM facts WHERE '+where+' AND name=? AND version<?',args+(name,max(1,version-9)))
        importance=1.0 if name in ('称呼','生日') else 0.8 if kind=='stable' else 0.5
        self.db.execute('INSERT INTO fact_metadata(scope,subject,name,kind,origin,importance) VALUES(?,?,?,?,?,?) ON CONFLICT(scope,subject,name) DO UPDATE SET kind=excluded.kind,origin=excluded.origin,importance=excluded.importance,last_recalled=0,recalls=0',(scope,subject,name,kind,origin,importance))
        return '长期事实已成功保存/更新' if origin=='explicit' else '本人明确自述已自动保存/更新'

    def observe(self,scope,subject,text,event,now,auto_capture=False):
        text=text.strip();status='没有新增长期事实'
        m=re.fullmatch(r'(记住|更正记忆|记住事件)[：:]\s*([^=＝\n]{1,40})[=＝](.{1,300})',text)
        if m:
            if m[1]=='更正记忆':
                from memory_reflection import invalidate
                invalidate(self.db,scope,subject)
            status=self.remember(scope,subject,m[2].strip(),m[3].strip(),event,now,kind='event' if m[1]=='记住事件' else 'stable')
        else:
            forget=re.fullmatch(r'忘记[：:]\s*(.{1,40})',text)
            if forget:
                from memory_reflection import invalidate
                invalidate(self.db,scope,subject)
                self.db.execute('INSERT OR IGNORE INTO memory_blocks VALUES(?,?,?)',(scope,subject,forget[1].strip()))
                where,args=person_filter(scope,subject)
                self.db.execute('UPDATE facts SET active=0 WHERE '+where+' AND name=?',args+(forget[1].strip(),));status='该事实已标记遗忘'
            elif auto_capture:
                report=self_report(text)
                if report:status=self.remember(scope,subject,*report,event,now,origin='self_report')
        return status

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
            if score>0:self.db.execute('UPDATE fact_metadata SET last_recalled=?,recalls=recalls+1 WHERE scope=? AND subject=? AND name=?',(now,r[10],subject,r[0]))
            if len(selected)>=limit:break
        self.db.commit();return selected

    def close(self):self.closed=True

    async def semantic_recall(self,scope,subject,query,now,cfg,provider,is_current,metrics=None):
        if self.closed or not is_current() or cfg.get('memory') is False:return []
        fallback=self.recall(scope,subject,query,now)
        model=cfg.get('embeddingModel','').strip();base_url=cfg.get('embeddingBaseUrl','').strip()
        if not cfg.get('memorySemantic') or cfg.get('memory') is False or not model or not query.strip() or provider is None or self.embedding_lock.locked():return fallback
        rows=[tuple(r[:6])+(r[7],r[10]) for r in fact_rows(self.db,scope,subject)]
        if not rows:return fallback
        epoch=self.db.execute('SELECT epoch FROM fact_vector_control WHERE id=1').fetchone()[0]
        settings=lambda:(cfg.get('embeddingModel','').strip(),cfg.get('embeddingBaseUrl','').strip(),bool(cfg.get('memorySemantic')),cfg.get('memory'))
        original=settings()
        def valid():
            return not self.closed and is_current() and settings()==original and self.db.execute('SELECT epoch FROM fact_vector_control WHERE id=1').fetchone()[0]==epoch
        def fingerprint(row):return hashlib.sha256(json.dumps(list(row[:5])+[row[7]],ensure_ascii=False).encode()).hexdigest()
        async with self.embedding_lock:
            try:
                if not valid():return []
                from embedding_turn import request
                space=hashlib.sha256(((base_url or str(provider.client.base_url)).rstrip('/')+'|'+model).encode()).hexdigest()
                where,args=person_filter(scope,subject)
                stored={(r[0],r[1]):r[2:] for r in self.db.execute('SELECT scope,name,version,fingerprint FROM fact_vectors WHERE '+where+' AND model=? LIMIT 200',args+(space,))}
                cached={r[0] for r in rows if (r[7],r[0]) in stored and stored[(r[7],r[0])][0]==r[3] and stored[(r[7],r[0])][1]==fingerprint(r)}
                missing=[r for r in rows if r[0] not in cached][:16]
                vectors=await request(provider,base_url,model,query,[r[0]+' '+r[1] for r in missing],self.db,cfg,metrics);dimension=len(vectors[0])
                if not 1<=dimension<=4096 or any(len(v)!=dimension or any(not isinstance(x,(int,float)) or not math.isfinite(x) for x in v) or not any(v) for v in vectors):raise ValueError('invalid embedding')
                # Lock the external admin writer across validation and commit.
                # Triggers also fence delete/reinsert with identical evidence.
                with self.db:
                    self.db.execute('BEGIN IMMEDIATE')
                    if not valid():return []
                    active={(r[10],r[0]):tuple(r[:5]) for r in fact_rows(self.db,scope,subject)}
                    if any(active.get((r[7],r[0]))!=tuple(r[:5]) for r in rows):return []
                    for row,vector in zip(missing,vectors[1:]):
                        cached.add(row[0])
                        self.db.execute('INSERT OR REPLACE INTO fact_vectors VALUES(?,?,?,?,?,?,?)',(row[7],subject,row[0],row[3],space,fingerprint(row),json.dumps(vector)))
                by_id={(r[7],r[0]):r for r in rows};ranked=[];a=vectors[0];norm=math.sqrt(sum(x*x for x in a))
                for source,name,version,fp,raw in self.db.execute('SELECT scope,name,version,fingerprint,vector FROM fact_vectors WHERE '+where+' AND model=? LIMIT 200',args+(space,)):
                    row=by_id.get((source,name))
                    if not row or version!=row[3] or fp!=fingerprint(row):continue
                    b=json.loads(raw)
                    if len(b)!=dimension or not all(isinstance(x,(int,float)) and math.isfinite(x) for x in b) or not any(b):continue
                    score=sum(x*y for x,y in zip(a,b))/(norm*math.sqrt(sum(y*y for y in b)))
                    if score>=.35:
                        ranked.append((score,row));ranked.sort(key=lambda x:(x[0],x[1][4]),reverse=True);del ranked[8:]
                semantic=[{'name':r[0],'value':r[1],'source_event':r[2],'version':r[3],'remembered_at':r[4],'kind':r[5],'origin':r[6],'source_scope':r[7],'subject':subject,'recall_reason':'semantic_match'} for similarity,r in ranked if similarity>=.35]
                selected=[];seen=set();used=0
                for item in semantic+fallback:
                    size=len(item['name'])+len(item['value'])
                    if item['name'] in seen or used+size>1600:continue
                    selected.append(item);seen.add(item['name']);used+=size
                    if len(selected)>=8:break
                return selected
            except asyncio.CancelledError:raise
            except Exception:
                if metrics is not None:metrics['embeddingFailures']=metrics.get('embeddingFailures',0)+1
                return fallback if valid() else []
