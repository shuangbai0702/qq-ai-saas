"""One bounded remote-embedding window per reply, with ephemeral query reuse."""
import asyncio,contextvars,hashlib,time

current=contextvars.ContextVar('hosted_embedding_turn',default=None)
slots=asyncio.Semaphore(2)

class EmbeddingTurn:
    def __init__(self,seconds=4.5):self.deadline=time.monotonic()+seconds;self.queries={}
    async def __aenter__(self):self.token=current.set(self);return self
    async def __aexit__(self,*_):current.reset(self.token);self.queries.clear()

async def request(provider,base_url,model,query,texts,db,cfg,metrics=None):
    from memory_reflection import embedding_request,reserve_embedding
    turn=current.get() or EmbeddingTurn()
    key=hashlib.sha256(((base_url or str(provider.client.base_url)).rstrip('/')+'|'+model+'|'+query[:800]).encode()).hexdigest()
    cached=turn.queries.get(key)
    if cached is not None and not texts:return [cached]
    remaining=turn.deadline-time.monotonic()
    if remaining<=0:raise TimeoutError('embedding_window_exhausted')
    inputs=([] if cached is not None else [query[:800]])+texts
    await asyncio.wait_for(slots.acquire(),remaining)
    try:
        remaining=turn.deadline-time.monotonic()
        if remaining<=0 or not reserve_embedding(db,cfg):raise TimeoutError('embedding_window_exhausted')
        if metrics is not None:metrics['embeddingRequests']=metrics.get('embeddingRequests',0)+1
        response=await asyncio.wait_for(embedding_request(provider,base_url,model,inputs),remaining)
    finally:slots.release()
    data=sorted(response.data,key=lambda x:x.index)
    if len(data)!=len(inputs) or [x.index for x in data]!=list(range(len(data))):raise ValueError('invalid embedding count')
    vectors=[x.embedding for x in data]
    if cached is None:
        turn.queries[key]=vectors[0]
        return vectors
    return [cached]+vectors
