"""Single dominant emotion: directed evidence, priority, repair and neutral release.
Mechanism aligned with DSH emotion; all transitions are deterministic and bounded.
"""
import re,json,time,math
LABELS={'calm':'平静','angry':'有点生气','sad':'有点委屈','worried':'担心','curious':'好奇','surprised':'惊讶','excited':'兴奋','happy':'开心','proud':'有点得意','awkward':'有点尴尬'}
TTL={'angry':1200,'sad':1800,'worried':1800,'curious':600,'surprised':600,'excited':1200,'happy':1800,'proud':900,'awkward':600,'calm':0}

def initialize(db):
    db.execute('CREATE TABLE IF NOT EXISTS companion_emotion(scope TEXT PRIMARY KEY,kind TEXT,intensity INTEGER,updated REAL,expires REAL,neutral INTEGER)')
    db.execute('CREATE TABLE IF NOT EXISTS companion_mood(scope TEXT PRIMARY KEY,valence REAL,energy REAL,updated REAL,event TEXT)')


def retire_sources(db,identities):
    from context_protocol import scoped_event_hash
    for scope,event in db.execute('SELECT scope,event FROM companion_mood').fetchall():
        if (scope,str(event)) in identities:db.execute('UPDATE companion_mood SET event=? WHERE scope=?',('semantic:'+scoped_event_hash(scope,str(event)),scope))


def mood_context(db,scope,now):
    row=db.execute('SELECT valence,energy,updated FROM companion_mood WHERE scope=?',(scope,)).fetchone()
    decay=2**(-max(0,now-row[2])/7200) if row else 0
    val=row[0]*decay if row else 0
    return {'valence':round(val,2),'label':'愉快' if val>.12 else '低落' if val<-.12 else '平静','energy':round(row[1]*decay,2) if row else 0}


def state(db,scope):
    row=db.execute('SELECT kind,intensity,updated,expires,neutral FROM companion_emotion WHERE scope=?',(scope,)).fetchone()
    return dict(zip(('kind','intensity','updated','expires','neutral'),row)) if row else None


def user_version(row):
    from memory_policy import version
    return version({k:row.get(k) for k in ('kind','intensity','updated','expires','neutral')})


def edit_by_user(db,data):
    """Temporary cognition correction; no fabricated conversation evidence."""
    if not data.get('actorId'):return {'error':'缺少真实用户操作来源','status':403}
    row=state(db,data['scope'])
    if not row or user_version(row)!=data.get('expectedVersion'):return {'error':'心境已变化，请刷新后重试','status':409}
    kind,strength,expires=data.get('kind'),data.get('intensity'),data.get('expires');now=time.time()
    if kind not in LABELS or type(strength) is not int or not 0<=strength<=3 or type(expires) not in (int,float) or not math.isfinite(expires):return {'error':'心境字段无效','status':400}
    if kind=='calm':strength=0;expires=0
    elif not 1<=strength<=3 or not now<expires<=now+TTL[kind]:return {'error':'情绪有效期应在该情绪的短期时限内','status':400}
    if 'user_edit' not in {r[1] for r in db.execute('PRAGMA table_info(companion_emotion)')}:db.execute('ALTER TABLE companion_emotion ADD COLUMN user_edit TEXT')
    db.execute('UPDATE companion_emotion SET kind=?,intensity=?,updated=?,expires=?,neutral=0,user_edit=? WHERE scope=?',(kind,strength,max(now,row['updated']+.000001),expires,json.dumps({'actorId':data['actorId'],'at':now}),data['scope']))
    # Mood is the same product's decaying projection, not a second memory.
    db.execute('UPDATE companion_mood SET valence=?,energy=?,updated=?,event=? WHERE scope=?',(_valence(kind,strength),strength/3,now,'platform-user-edit',data['scope']))
    return {'ok':True,'updatedRegion':'emotion','version':user_version(state(db,data['scope']))}


def _valence(kind,strength):return (-1 if kind in ('angry','sad','worried','awkward') else 0 if kind=='calm' else 1)*strength/6


def apply_summary(db,scope,item,source,before,now):
    """Caller supplies verified evidence and the legal compression transaction."""
    current=state(db,scope)
    previous=dict(zip(('kind','intensity','updated','expires','neutral'),before)) if before else None
    if not source or current!=previous or item.get('kind') not in LABELS or type(item.get('intensity')) is not int or not 1<=item['intensity']<=3:return False
    kind=item['kind'];strength=0 if kind=='calm' else item['intensity']
    db.execute('INSERT OR REPLACE INTO companion_emotion(scope,kind,intensity,updated,expires,neutral) VALUES(?,?,?,?,?,0)',(scope,kind,strength,now,now+TTL[kind] if strength else now))
    db.execute('INSERT OR REPLACE INTO companion_mood VALUES(?,?,?,?,?)',(scope,_valence(kind,strength),strength/3,now,source['event']))
    return True

def context(db,scope,now):
    row=db.execute('SELECT kind,intensity,expires FROM companion_emotion WHERE scope=?',(scope,)).fetchone()
    kind=row[0] if row and row[2]>now else 'calm'
    return {'kind':kind,'label':LABELS[kind],'intensity':row[1] if kind!='calm' else 0,'expires':row[2] if kind!='calm' else 0}
