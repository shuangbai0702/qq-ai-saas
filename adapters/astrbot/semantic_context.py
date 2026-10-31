"""Bounded semantic reading in the ordinary reply model, without extra calls.
Vector retrieval remains available when a remote embedding model is configured.
Every candidate retains its scope, attribution and revision evidence.
"""
import json,time

def recall(store,scope,subject,query,cfg,current,metrics):
    if store.memory.closed or not current():return [],[]
    facts=store.memory.recall(scope,subject,query,time.time())
    # Keep relevant lexical matches first, then allow the reply model to read
    # recent candidates whose wording differs from the current question.
    rows=store.db.execute('SELECT name,value,event,version,updated FROM facts WHERE scope=? AND subject=? AND active=1 ORDER BY updated DESC LIMIT 16',(scope,subject)).fetchall()
    candidates=store.search.search(scope,subject,query)+facts+[{'name':r[0],'value':r[1],'source_event':r[2],'version':r[3],'remembered_at':r[4]} for r in rows]
    selected=[];seen=set();used=0
    for item in candidates:
        size=len(item['name'])+len(item['value'])
        if item['name'] in seen or used+size>1600:continue
        selected.append({**item,'recall_reason':item.get('recall_reason','model_semantic_candidate'),'semantic_mode':'model_semantic_candidate'});seen.add(item['name']);used+=size
        if len(selected)>=16:break
    summaries=[]
    if cfg.get('memorySummary'):
        old=store.search.search(scope,subject,query,summaries=True)+store.reflection.recall(scope,query)
        rows=store.db.execute('SELECT id,text,sources,updated FROM memory_summaries WHERE scope=? ORDER BY updated DESC LIMIT 8',(scope,)).fetchall()
        seen=set();used=0
        for item in old+[{'id':r[0],'summary':r[1],'source_events':json.loads(r[2]),'at':r[3],'kind':'conversation_summary_not_verified_facts'} for r in rows]:
            if item['id'] in seen:continue
            text=item['summary'][:300]
            if used+len(text)>1200:continue
            summaries.append({**item,'summary':text,'recall_reason':item.get('recall_reason','model_semantic_candidate')});seen.add(item['id']);used+=len(text)
            if len(summaries)>=5:break
    metrics['semanticContextTurns']=metrics.get('semanticContextTurns',0)+1
    metrics['semanticContextCandidates']=len(selected)+len(summaries)
    return selected,summaries
