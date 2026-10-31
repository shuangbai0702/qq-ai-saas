"""Single dominant emotion: directed evidence, priority, repair and neutral release.
Mechanism aligned with DSH emotion; all transitions are deterministic and bounded.
"""
import re
LABELS={'calm':'平静','angry':'有点生气','sad':'有点委屈','worried':'担心','curious':'好奇','surprised':'惊讶','excited':'兴奋','happy':'开心','proud':'有点得意','awkward':'有点尴尬'}
TTL={'angry':1200,'sad':1800,'worried':1800,'curious':600,'surprised':600,'excited':1200,'happy':1800,'proud':900,'awkward':600,'calm':0}

def initialize(db):
    db.execute('CREATE TABLE IF NOT EXISTS companion_emotion(scope TEXT PRIMARY KEY,kind TEXT,intensity INTEGER,updated REAL,expires REAL,neutral INTEGER)')

def observe(db,scope,text,now,directed):
    from behavior import emotional_phrase
    row=db.execute('SELECT kind,intensity,updated,expires,neutral FROM companion_emotion WHERE scope=?',(scope,)).fetchone()
    old=row[0] if row and row[3]>now else 'calm';strength=row[1] if old!='calm' else 0
    neutral=row[4] if row else 0;kind=None;intensity=2 if directed else 1
    # A quoted sentence or self-directed complaint is not an attack on the bot.
    quotation=bool(re.search(r'[“”"「」]|他说|她说|比如|例如|假设|翻译|复述',text))
    if directed and not quotation and re.search('对不起|抱歉|不是说你|误会了|刚才开玩笑',text):kind='calm';intensity=0
    elif directed and not quotation and emotional_phrase(text,'滚开|闭嘴|讨厌你|不喜欢你|你真蠢|你太笨'):kind='angry'
    elif not quotation:
        signals=[('sad','难过|伤心|失落'),('worried','担心|害怕|焦虑'),('surprised','居然|没想到|吓一跳'),('excited','太棒了|太激动了'),('happy','谢谢|喜欢你|做得好|辛苦了'),('proud','你真厉害|你很厉害')]
        for candidate,pattern in signals:
            if emotional_phrase(text,pattern):kind=candidate;break
        if kind is None and directed and re.search(r'[?？]|怎么|为什么|什么原因',text):kind='curious'
        if kind is None and re.fullmatch(r'[额呃]+[。…]*',text.strip()):kind='awkward'
    if kind is None:
        neutral+=1
        if old in ('angry','sad','worried','awkward') and neutral>=4:kind='calm';intensity=0
        else:return db.execute('UPDATE companion_emotion SET neutral=? WHERE scope=?',(neutral,scope))
    if not directed and strength>intensity:return
    db.execute('INSERT OR REPLACE INTO companion_emotion VALUES(?,?,?,?,?,?)',(scope,kind,intensity,now,now+TTL[kind],0))

def context(db,scope,now):
    row=db.execute('SELECT kind,intensity,expires FROM companion_emotion WHERE scope=?',(scope,)).fetchone()
    kind=row[0] if row and row[2]>now else 'calm'
    return {'kind':kind,'label':LABELS[kind],'intensity':row[1] if kind!='calm' else 0,'expires':row[2] if kind!='calm' else 0}

def answered(db,scope):
    db.execute("UPDATE companion_emotion SET kind='calm',intensity=0,expires=0,neutral=0 WHERE scope=? AND kind='curious'",(scope,))
