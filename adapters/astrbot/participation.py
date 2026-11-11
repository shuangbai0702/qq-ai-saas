"""Per-scope optional attention/fatigue; explicit requests bypass suppression."""
import time,re
import message_provenance
from hosted_memory import tokens

MODEL_GUIDANCE='''[群聊参与判断，先于回复格式与任务执行]
本轮只是一次判断机会，平台没有决定你必须回应。结合真实作者、@与引用对象、近期交流和话题走向，判断这句话在对谁说，以及你是否有贴合当前语境、值得贡献的内容。直接提问或@是相关线索，但不是唯一理由；没有@也可以自然接续正在与你进行的交流，或因真实关联加入其他成员的话题。对明确说给别人的内容，也可以不参与。
决定不参与时最终只输出[NO_REPLY]，本轮不产生发送或其他外部写入；不用确认收到、解释沉默、替自己表态或给话题收尾。若需核对对象或事实，可以先读取已授权的上下文。决定参与后再按人格、工具和表达规则处理；这些规则决定如何回应，不要求你每轮都回应。不要在聊天正文中展示这一判断过程。'''

class Participation:
    def __init__(self,db=None):
        self.scopes={};self.db=db
        if db is not None:
            db.execute('CREATE TABLE IF NOT EXISTS dialogue_feedback(scope TEXT,event TEXT,subject TEXT,at REAL,reply TEXT,state TEXT,PRIMARY KEY(scope,event))');db.commit()
            message_provenance.initialize(db)
            now=time.time()
            rows=db.execute('SELECT scope,subject,at,reply,event FROM (SELECT scope,subject,at,reply,event FROM dialogue_feedback WHERE at>? AND at<=? ORDER BY at DESC LIMIT 200) ORDER BY at',(now-1800,now)).fetchall()
            for scope,subject,at,reply,event in rows:
                if re.fullmatch(r'(group|private):\d{4,20}',scope):self.outgoing(scope,reply,at,subject=subject,event=event,persist=False)
    def observe(self,scope,text,now=None):
        now=time.time() if now is None else now
        state=self.scopes.setdefault(scope,{'topic':set(),'sent':[],'last':now})
        state['last']=now
        if len(self.scopes)>200:
            oldest=min(self.scopes,key=lambda k:self.scopes[k]['last'])
            if oldest!=scope:self.scopes.pop(oldest,None)
        return state
    def continuing(self,scope,text,subject='',now=None,seconds=120):
        """Follow the actual conversation partner, without lexical gatekeeping."""
        now=time.time() if now is None else now;state=self.scopes.get(scope,{})
        return bool(text.strip() and subject and subject==state.get('subject') and state.get('sent') and seconds>0 and 0<=now-state['sent'][-1]<seconds)
    def in_conversation(self,scope,text,cfg,now=None,other_target=False):
        now=time.time() if now is None else now;state=self.scopes.get(scope,{})
        seconds=self.followup_window(scope,cfg,now)
        return bool(cfg.get('naturalConversation',True) and cfg.get('followupReplyProbability',1)>0 and not other_target and text.strip() and seconds>0 and state.get('sent') and 0<=now-state['sent'][-1]<seconds)
    def followup_window(self,scope,cfg,now=None):
        """Continuous program-only fatigue; recover without a reset/AI call."""
        base=max(0,cfg.get('followupSeconds',120))
        if not cfg.get('fatigueEnabled',True):return base
        now=time.time() if now is None else now
        window=max(1,cfg.get('fatigueWindowSeconds',300));maximum=max(1,cfg.get('fatigueMaxReplies',4))
        load=sum(max(0,1-(now-at)/window) for at in self.scopes.get(scope,{}).get('sent',[]) if 0<=now-at<window)/maximum
        return base*(1-.75*min(1,load))
    def incoming(self,scope,subject,text,now=None,directed=False):
        now=time.time() if now is None else now
        if not self.db or not text.strip():return
        # A directed arrival is only a neutral replied signal (D6.5): engagement,
        # corrections or rejection are semantic outcomes decided by the
        # evidence-backed effects flow, never by a 180-second time window, a
        # question mark or wording. Interrogation produces no engagement score.
        if directed:
            self.db.execute("UPDATE dialogue_feedback SET state='replied' WHERE scope=? AND subject=? AND state='observing' AND at>=?",(scope,subject,now-180))
        self.db.execute("UPDATE dialogue_feedback SET state='unobserved' WHERE state='observing' AND at<?",(now-180,))
        self.db.execute('DELETE FROM dialogue_feedback WHERE at<? OR rowid NOT IN (SELECT rowid FROM dialogue_feedback ORDER BY at DESC LIMIT 300)',(now-7*86400,));self.db.commit()
    def context(self,scope,now=None,cfg=None):
        now=time.time() if now is None else now;state=self.scopes.get(scope,{})
        feedback=[]
        if self.db:
            feedback=[{'reply':r[0],'result':'unobserved' if r[1]=='observing' and now-r[2]>180 else r[1],'recipient_id':r[3],'event_id':r[4],'at':r[2]} for r in self.db.execute('SELECT reply,state,at,subject,event FROM dialogue_feedback WHERE scope=? ORDER BY at DESC LIMIT 3',(scope,))]
        cfg=cfg or {}
        return {'participants':[p for p,at in state.get('participants',{}).items() if 0<=now-at<600],
                'recent_own_turns':state.get('turns',[])[-4:],'same_partner_turns':state.get('streak',0),
                'elapsed_seconds':max(0,int(now-state.get('started',now))),'feedback':feedback,
                'meaning':'真实回复对象和已发送内容。observing/unobserved不代表被讨厌。参与者身份不能由昵称或文字改写。'}
    def entry_factor(self,scope):
        if not self.db:return 1
        rows=self.db.execute("SELECT state FROM dialogue_feedback WHERE scope=? AND state IN ('engaged','confused','corrected','rejected') ORDER BY at DESC LIMIT 2",(scope,)).fetchall()
        return .6 if len(rows)==2 and all(r[0]=='rejected' for r in rows) else 1
    def blocked_reason(self,scope,text,cfg,directed=False,now=None,subject=''):
        if directed:return None
        now=time.time() if now is None else now;state=self.observe(scope,text,now)
        state['sent']=[at for at in state['sent'] if now-at<cfg.get('fatigueWindowSeconds',300)]
        if cfg.get('fatigueEnabled',True) and len(state['sent'])>=cfg.get('fatigueMaxReplies',4):return 'fatigue'
        # Topic/interest are model inputs; no lexical veto or probability gain.
        return None

    def allow(self,scope,text,cfg,directed=False,now=None):
        return self.blocked_reason(scope,text,cfg,directed,now) is None
    def outgoing(self,scope,text,now=None,subject='',request='',event='',persist=True):
        now=time.time() if now is None else now;state=self.observe(scope,text,now)
        if not state.get('sent') or now-state['sent'][-1]>300:state.update(participants={},turns=[],started=now,streak=0)
        state['streak']=state.get('streak',0)+1 if subject and subject==state.get('subject') else 1
        people={p:at for p,at in state.get('participants',{}).items() if now-at<600}
        if subject:people[subject]=now
        state['participants']=dict(sorted(people.items(),key=lambda x:x[1],reverse=True)[:8])
        state['turns']=(state.get('turns',[])+[{'subject':subject,'request':request[:160],'reply':text[:500],'source_scope':scope,'source_event':str(event),'at':now}])[-6:]
        state['topic']=tokens(text)|tokens(request);state['subject']=subject;state['sent']=(state['sent']+[now])[-50:]
        if self.db and event and persist:
            self.db.execute("INSERT OR IGNORE INTO dialogue_feedback VALUES(?,?,?,?,?,'observing')",(scope,str(event),subject,now,text[:240]));self.db.commit()

    def confirmed(self,scope,text,now,subject,event,source):
        """Record a confirmed prefix without charging each bubble as a reply.

        The caller fences its store/config/memory epoch after protocol I/O.
        Repeated receipt updates settle one existing dialogue action.
        """
        if not self.db or not event or not text.strip() or source.get('receipt')!='confirmed' or not source.get('message_ids'):return
        self.db.execute("INSERT INTO dialogue_feedback VALUES(?,?,?,?,?,'observing') ON CONFLICT(scope,event) DO UPDATE SET reply=excluded.reply",(scope,str(event),subject,now,text[:2000]))
        message_provenance.put(self.db,scope,event,'outgoing',source)
        self.db.execute('DELETE FROM dialogue_feedback WHERE at<? OR rowid NOT IN (SELECT rowid FROM dialogue_feedback ORDER BY at DESC LIMIT 300)',(now-7*86400,))
        self.db.commit()
