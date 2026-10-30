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
    def allow(self,scope,text,cfg,directed=False,now=None):
        if directed:return True
        now=time.time() if now is None else now;state=self.observe(scope,text,now)
        state['sent']=[at for at in state['sent'] if now-at<cfg.get('fatigueWindowSeconds',300)]
        if cfg.get('fatigueEnabled') and len(state['sent'])>=cfg.get('fatigueMaxReplies',4):return False
        if cfg.get('attentionEnabled') and state['topic']:
            query=tokens(text)
            # Only ongoing conversation is constrained; initial entry remains eligible.
            if state['sent'] and now-state['sent'][-1]<120 and not query&state['topic']:return False
        return True
    def outgoing(self,scope,text,now=None):
        now=time.time() if now is None else now;state=self.observe(scope,text,now)
        state['topic']=tokens(text);state['sent']=(state['sent']+[now])[-50:]
