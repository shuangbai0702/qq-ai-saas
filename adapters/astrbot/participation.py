"""Per-scope optional attention/fatigue; explicit requests bypass suppression."""
import time
from hosted_memory import tokens

class Participation:
    def __init__(self):self.scopes={}
    def observe(self,scope,text,now=None):
        now=time.time() if now is None else now
        state=self.scopes.setdefault(scope,{'topic':set(),'sent':[],'last':now})
        state['last']=now
        if len(self.scopes)>200:
            oldest=min(self.scopes,key=lambda k:self.scopes[k]['last'])
            if oldest!=scope:self.scopes.pop(oldest,None)
        return state
    def blocked_reason(self,scope,text,cfg,directed=False,now=None):
        if directed:return None
        now=time.time() if now is None else now;state=self.observe(scope,text,now)
        state['sent']=[at for at in state['sent'] if now-at<cfg.get('fatigueWindowSeconds',300)]
        if cfg.get('fatigueEnabled',True) and len(state['sent'])>=cfg.get('fatigueMaxReplies',4):return 'fatigue'
        if cfg.get('attentionEnabled') and state['topic']:
            query=tokens(text)
            interest=set().union(*(tokens(x.get('topic','')) for x in cfg.get('_interests',[]) if x.get('source') in ('persona','bot_expression')))
            # Related interests and short conversational acknowledgements may
            # shift focus without a second model call or a lexical hard veto.
            if query&interest or len(text.strip())<=12 and __import__('re').search(r'^(是啊|对啊|确实|哈哈|然后呢|接着说|为什么|怎么说|还有呢|嗯|好呀|真的)',text.strip()):return None
            # Only ongoing conversation is constrained; initial entry remains eligible.
            if state['sent'] and now-state['sent'][-1]<120 and not query&state['topic']:return 'attention'
        return None
    def allow(self,scope,text,cfg,directed=False,now=None):
        return self.blocked_reason(scope,text,cfg,directed,now) is None
    def outgoing(self,scope,text,now=None):
        now=time.time() if now is None else now;state=self.observe(scope,text,now)
        state['topic']=tokens(text);state['sent']=(state['sent']+[now])[-50:]
