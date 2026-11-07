"""Bounded semantic reading in the ordinary reply model, without extra calls.
Vector retrieval remains available when a remote embedding model is configured.
Every candidate retains its scope, attribution and revision evidence.
"""
import json,time
from memory_scope import fact_rows,fact_item,summary_rows,summary_item,memory_audience

def recall(store,scope,subject,query,cfg,current,metrics):
    if store.memory.closed or not current():return [],[]
    owner=str(cfg.get('ownerUin','') or '')
    context={'scope':scope,'subject':subject,'owner':owner}
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
        old=[item for item in store.search.search(scope,subject,query,summaries=True)+store.reflection.recall(scope,query,subject=subject) if memory_audience(context,item)]
        rows=[r for r in summary_rows(store.db,scope,subject,limit=8) if memory_audience(context,{'source_scope':r[4]})]
        seen=set();used=0;shown_claims=set()
        for item in old+[summary_item(r,'model_semantic_candidate',db=store.db) for r in rows]:
            if item['id'] in seen:continue
            text=item['summary'][:300]
            # The request budget covers what is actually attached: each
            # protected claim rides inside this measured limit, once per id.
            claims=[c for c in item.pop('protected_claims',None) or [] if isinstance(c,dict) and c.get('id') and c['id'] not in shown_claims]
            def claim_size(claim):return len(json.dumps(claim,ensure_ascii=False,separators=(',',':')))
            if used+len(text)>1200:continue
            kept=[];spent=0
            for claim in claims:
                size=claim_size(claim)
                if spent+size>1200-used-len(text):break
                kept.append(claim);spent+=size
            if kept:
                item['protected_claims']=kept;shown_claims.update(c['id'] for c in kept)
            summaries.append({**item,'summary':text,'recall_reason':item.get('recall_reason','model_semantic_candidate')});seen.add(item['id']);used+=len(text)+spent
            if len(summaries)>=5:break
    metrics['semanticContextTurns']=metrics.get('semanticContextTurns',0)+1
    metrics['semanticContextCandidates']=len(selected)+len(summaries)
    # The current conversation always sees its rolling big + pending small
    # summaries. Cross-session recall remains attributed and bounded above.
    rolling=store.reflection.rolling(scope,owner=owner)
    local_ids={item['id'] for item in rolling}
    # The rolling window carries the newest audience-filtered material; topic
    # matches outside that window (including this scope's own older
    # conventions) remain visible after it instead of disappearing.
    # Query-matched summaries rank before the rolling background so an exact
    # rare topic is not buried; the rolling window stays fully visible after.
    rolling_ids={item['id'] for item in rolling}
    matched=[item for item in summaries if item['id'] not in rolling_ids]
    summaries=matched+rolling
    # One copy per claim id across the whole reply view: a claim already shown
    # by a query-matched summary is not repeated by the rolling background.
    shown={c.get('id') for item in matched for c in (item.get('protected_claims') or []) if isinstance(c,dict)}
    for item in rolling:
        claims=item.get('protected_claims')
        if not claims:continue
        kept=[c for c in claims if isinstance(c,dict) and c.get('id') not in shown]
        shown.update(c['id'] for c in kept)
        if len(kept)!=len(claims):
            if kept:item['protected_claims']=kept
            else:item.pop('protected_claims',None)
    return selected,summaries
