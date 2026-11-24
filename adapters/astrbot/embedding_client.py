"""Single owner for provider embedding requests and daily embedding budget."""
import asyncio,time
from types import SimpleNamespace
from model_cache import auxiliary_scope,adopted,begin_attempt,finish_attempt,reset_attempt


def initialize(db):
    db.execute('CREATE TABLE IF NOT EXISTS memory_embedding_budget(day INTEGER PRIMARY KEY,count INTEGER)')


async def _embedding_request(provider,base_url,model,texts):
    if not base_url:
        return await provider.client.with_options(max_retries=0,timeout=5).embeddings.create(model=model,input=texts)
    # Borrow only the active Bot key; never persist it or create an unguarded SDK.
    from openai import AsyncOpenAI
    from model_egress import client
    http=client({'api_base':base_url})
    try:
        async with AsyncOpenAI(api_key=provider.client.api_key,base_url=base_url,http_client=http,max_retries=0,timeout=5) as sdk:
            return await sdk.embeddings.create(model=model,input=texts)
    finally:
        await http.aclose()


async def embedding_request(provider,base_url,model,texts):
    with auxiliary_scope(provider,'embedding'):
        token=begin_attempt(None,provider)
        try:
            response=await _embedding_request(provider,base_url,model,texts)
            finish_attempt('completed',SimpleNamespace(raw_completion=response))
            adopted('embedding_response_returned')
            return response
        except BaseException as error:
            finish_attempt('canceled' if isinstance(error,asyncio.CancelledError) else 'failed',reason=type(error).__name__)
            raise
        finally:reset_attempt(token)


def reserve_embedding(db,cfg):
    initialize(db)
    # Fact/meme embedding budget; charge before the network await.
    day=int((time.time()+8*3600)//86400)
    limit=max(0,min(1000,int(cfg.get('dailyEmbeddingLimit',100))))
    used=db.execute('SELECT count FROM memory_embedding_budget WHERE day=?',(day,)).fetchone()
    if (used[0] if used else 0)>=limit:return False
    db.execute('INSERT INTO memory_embedding_budget VALUES(?,1) ON CONFLICT(day) DO UPDATE SET count=count+1',(day,))
    db.execute('DELETE FROM memory_embedding_budget WHERE day<?',(day-7,));db.commit()
    return True
