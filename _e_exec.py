import sqlite3,json,sys
BOT=sys.argv[1]
con=sqlite3.connect('file:/var/lib/fishbot/bots/%s/behavior.sqlite?mode=ro'%BOT,uri=True)
row=con.execute("SELECT payload FROM fishbot_readonly_journals WHERE state='ready' AND scope LIKE '%private:%' ORDER BY updated DESC LIMIT 1").fetchone()
st=(json.loads(row[0]).get('state') or {})
ex=st.get('execution_experience') or {}
print('exec_exp keys=',sorted(ex.keys()))
def sz(v): return len(json.dumps(v,ensure_ascii=False,separators=(',' ,':')))
for k in sorted(ex.keys()): print('  %-24s %5d'%(k,sz(ex.get(k))))
print('exec_exp view=',ex.get('view'),'meaning_len=',len(ex.get('meaning','')))
tl=st.get('tool_context')
print('tool_context type=',type(tl).__name__,'keys=',sorted(tl.keys())[:8] if isinstance(tl,dict) else (len(tl) if isinstance(tl,list) else tl))
if isinstance(tl,dict):
    for k in sorted(tl.keys()): print('  %-24s %5d'%(k,sz(tl.get(k))))
soc=st.get('social') or {}
if isinstance(soc,dict):
    print('social keys=',[(k,sz(soc[k])) for k in sorted(soc.keys())])
dlg=st.get('dialogue') or {}
if isinstance(dlg,dict):
    print('dialogue keys=',[(k,sz(dlg[k])) for k in sorted(dlg.keys())])
