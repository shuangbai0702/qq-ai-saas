"""Real native manager: turn ownership, tool chains and token guard."""
import asyncio,copy,sys,unittest
from pathlib import Path
from types import SimpleNamespace as NS
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'references/astrbot'),str(ROOT/'adapters/astrbot')]
from astrbot.core.agent.context.config import ContextConfig
from astrbot.core.agent.context.manager import ContextManager
from astrbot.core.agent.context.truncator import ContextTruncator
from astrbot.core.agent.message import Message
from native_history import LogicalTurnLimiter,bind_history_rounds


def history(rounds=3,steps=5):
    rows=[Message(role='system',content='current authority')]
    for i in range(rounds):
        rows.append(Message(role='user',content='user-'+str(i)))
        for j in range(steps):
            ident=f'{i}-{j}'
            rows.extend([Message(role='assistant',tool_calls=[{'id':ident,'type':'function','function':{'name':'read','arguments':'{}'}}]),Message(role='tool',tool_call_id=ident,content='unknown write: '+ident)])
        rows.append(Message(role='assistant',content='answer-'+str(i)))
    return rows


class History(unittest.IsolatedAsyncioTestCase):
    def limiter(self):return LogicalTurnLimiter(ContextTruncator())

    def test_tools_do_not_evict_unsummarized_turns_early(self):
        rows=history();before=copy.deepcopy(rows)
        self.assertLess(len(ContextTruncator().truncate_by_turns(rows,3)),len(rows))
        self.assertIs(self.limiter().truncate_by_turns(rows,3),rows)
        self.assertEqual(rows,before)

    def test_limit_and_batch_drop_keep_complete_chains_and_authority(self):
        rows=history(5);result=self.limiter().truncate_by_turns(rows,3)
        self.assertEqual(result,[rows[0]]+rows[25:])
        self.assertEqual([r.content for r in result if r.role=='user'],['user-2','user-3','user-4'])
        for message in result:
            if message.role=='tool':
                self.assertTrue(any(r.tool_calls and r.tool_calls[0].id==message.tool_call_id for r in result))
        self.assertEqual([r.content for r in self.limiter().truncate_by_turns(rows,3,2) if r.role=='user'],['user-3','user-4'])
        self.assertIs(self.limiter().truncate_by_turns(rows,-1),rows)

    async def test_per_runner_scope_and_token_pressure_are_unchanged(self):
        manager=ContextManager(ContextConfig(enforce_max_turns=3,max_context_tokens=100))
        other=ContextManager(ContextConfig(enforce_max_turns=3))
        runner=NS(request_context_manager=manager);bind_history_rounds(runner);first=manager.truncator
        bind_history_rounds(runner);self.assertIs(manager.truncator,first)
        self.assertIs(type(other.truncator),ContextTruncator)
        class Pressure:
            called=False
            def should_compress(self,messages,tokens,maximum):return not self.called
            async def __call__(self,messages):self.called=True;return messages[:1]+messages[-1:]
        pressure=Pressure();manager.compressor=pressure
        result=await manager.process(history())
        self.assertTrue(pressure.called);self.assertEqual(len(result),2)
        self.assertEqual(manager.config.max_context_tokens,100)

    def test_unknown_leading_chain_uses_native_fallback(self):
        rows=[Message(role='assistant',content='legacy')]+history()[1:]
        self.assertEqual(self.limiter().truncate_by_turns(rows,2),ContextTruncator().truncate_by_turns(rows,2))


if __name__=='__main__':unittest.main()
