"""Adapt deterministic complete responses to the pinned streaming contract."""
from types import SimpleNamespace

def summary_provider(chat):
    async def stream(**kwargs):
        response=await chat(**kwargs)
        if getattr(response,'raw_completion',None) is None:
            response.raw_completion={'choices':[{'finish_reason':'stop'}]}
        yield response
    return SimpleNamespace(text_chat=chat,text_chat_stream=stream)
