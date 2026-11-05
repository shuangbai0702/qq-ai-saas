"""Bounded semantic reading in the ordinary reply model, without extra calls.
Vector retrieval remains available when a remote embedding model is configured.
Every candidate retains its scope, attribution and revision evidence.
"""
import json,time
from memory_scope import fact_rows,fact_item,summary_rows,summary_item

def recall(store,scope,subject,query,cfg,current,metrics):
    if store.memory.closed or not current():return [],[]
    facts=store.memory.recall(scope,subject,query,time.time())
    # Keep relevant lexical matches first, then allow the reply model to read
    # recent candidates whose wording differs from the current question.
    rows=fact_rows(store.db,scope,subject,limit=16)
    candidates=store.search.search(scope,subject,query)+facts+[fact_item(r,subject,'model_semantic_candidate') for r in rows]
    selected=[];seen=set();used=0
    for item in candidates:
        size=len(item['name'])+len(item['value'])
        if item['name'] in seen or used+size>1600:continue
        selected.append({**item,'recall_reason':item.get('recall_reason','model_semantic_candidate'),'semantic_mode':'model_semantic_candidate'});seen.add(item['name']);used+=size
        if len(selected)>=16:break
    summaries=[]
    if cfg.get('memorySummary'):
        old=store.search.search(scope,subject,query,summaries=True)+store.reflection.recall(scope,query,subject=subject)
        rows=summary_rows(store.db,scope,subject,limit=8)
        seen=set();used=0
        for item in old+[summary_item(r,'model_semantic_candidate') for r in rows]:
            if item['id'] in seen:continue
            text=item['summary'][:300]
            if used+len(text)>1200:continue
            summaries.append({**item,'summary':text,'recall_reason':item.get('recall_reason','model_semantic_candidate')});seen.add(item['id']);used+=len(text)
            if len(summaries)>=5:break
    metrics['semanticContextTurns']=metrics.get('semanticContextTurns',0)+1
    metrics['semanticContextCandidates']=len(selected)+len(summaries)
    # The current conversation always sees its rolling big + pending small
    # summaries. Cross-session recall remains attributed and bounded above.
    rolling=store.reflection.rolling(scope)
    local_ids={item['id'] for item in rolling}
    summaries=rolling+[item for item in summaries if item['id'] not in local_ids and item.get('source_scope')!=scope]
    return selected,summaries
