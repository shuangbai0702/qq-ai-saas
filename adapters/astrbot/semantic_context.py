"""Bounded semantic reading in the ordinary reply model, without extra calls.
Vector retrieval remains available when a remote embedding model is configured.
Every candidate retains its scope, attribution and revision evidence.
"""
import time
from memory_scope import fact_rows,fact_item

def recall(store,scope,subject,query,cfg,current,metrics):
    if store.memory.closed or not current():return [],[]
    owner=str(cfg.get('ownerUin','') or '')
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
    metrics['semanticContextTurns']=metrics.get('semanticContextTurns',0)+1
    metrics['semanticContextCandidates']=len(selected)
    return selected,store.reflection.foundation(scope,owner=owner) if cfg.get('memorySummary') else []
