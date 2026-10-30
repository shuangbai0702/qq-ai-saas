"""Bounded output pacing, informed by GroupChatPlus's published settings.
No input-trigger changes and no model calls. Only ordinary Chinese chat gets typos.
"""
import random,re,asyncio,time


def typing_delay(text,cfg,rng=random.random):
    if not cfg.get('typingSimulation'):return 0
    if len(text)<=3 or any(x in text for x in ('[',']','```','{','}')):return .25
    maximum=cfg.get('typingMaxDelay',3)
    return max(min(.5,maximum),min(maximum,len(text)/max(5,cfg.get('typingSpeed',15))*(.7+.6*rng())))


def conversational_typo(text,cfg,rng=random.random):
    # One homophone at most, never numbers, URLs, paths, code or instructions.
    # Small curated mapping avoids a resident pinyin dictionary/dependency.
    if not cfg.get('typoEnabled') or len(text)<10 or len(text)>160 or re.search(r'[A-Za-z0-9/\\{}\[\]`]|密码|账号|地址|药|剂量|命令|签名|昵称|记住|更正',text):return text
    if rng()>=cfg.get('typoProbability',.02):return text
    pairs={'的':'滴','吧':'叭','吗':'嘛','呀':'鸭'}
    positions=[i for i,c in enumerate(text) if c in pairs]
    if not positions:return text
    i=positions[min(len(positions)-1,int(rng()*len(positions)))]
    return text[:i]+pairs[text[i]]+text[i+1:]


async def poke(event,cfg,db,current,rng=random.random,now=None):
    raw=getattr(event.message_obj,'raw_message',{}) or {}
    if raw.get('post_type')!='notice':return False
    # Notices must never fall through into a paid model turn.
    b=cfg.get('behavior') or {};now=time.time() if now is None else now
    if not b.get('pokeReply') or raw.get('sub_type')!='poke' or str(raw.get('target_id'))!=str(event.get_self_id()) or str(raw.get('user_id'))!=str(event.get_sender_id()) or str(event.get_sender_id())==str(event.get_self_id()):return True
    group=str(event.get_group_id() or '')
    if group and cfg.get('groups') and group not in cfg['groups']:return True
    db.execute('CREATE TABLE IF NOT EXISTS poke_cooldown(scope TEXT PRIMARY KEY,at REAL)')
    scope='group:'+group if group else 'private:'+str(event.get_sender_id())
    row=db.execute('SELECT at FROM poke_cooldown WHERE scope=?',(scope,)).fetchone()
    if row and now-row[0]<60:return True
    if rng()>=b.get('pokeProbability',.25) or not current():return True
    db.execute('INSERT OR REPLACE INTO poke_cooldown VALUES(?,?)',(scope,now));db.execute('DELETE FROM poke_cooldown WHERE at<?',(now-86400,));db.commit()
    try:
        params={'user_id':str(event.get_sender_id())}
        if group:params['group_id']=group
        if current():await asyncio.wait_for(event.bot.send_poke(**params),5)
    except asyncio.CancelledError:raise
    except Exception:pass  # Optional protocol action; never model-fallback or retry.
    return True
