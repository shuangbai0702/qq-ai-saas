"""Installed SDK boundaries: count/retract only the owning protocol attempt."""
import asyncio,os,sys,tempfile,time
from pathlib import Path
from types import SimpleNamespace as NS
root=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(root/'references/astrbot'),str(root/'adapters/astrbot')]
temp=tempfile.TemporaryDirectory();os.environ['ASTRBOT_ROOT']=temp.name
import runtime_bootstrap
runtime_bootstrap.initialize_runtime_bootstrap()
from slim_bootstrap import install
install()
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent
from astrbot.core.platform.astr_message_event import current_message_event
from astrbot.api.event import MessageChain
from astrbot.core.message.components import Plain
from group_freshness import PreDispatchRejected
from behavior import install_runtime


async def run():
    async def rejected_outer(event,message):raise PreDispatchRejected('no_dispatch_entered')
    AiocqhttpMessageEvent.send=rejected_outer
    settings={};runtime=install_runtime(NS(event_bus=NS(stopped_platforms=set())),settings)
    def fresh(name):
        runtime['release']('qq')
        lease={'root':str(Path(temp.name)/name),'botId':'bot','generation':1,'config':{'behavior':{'memory':False,'typingSimulation':False,'memeEnabled':False,'qqFaces':False}}};settings['qq']=lease
        extra={'_hosted_lease':lease,'_hosted_generation':1}
        event=NS(message_obj=NS(message_id=name,timestamp=time.time()),get_platform_id=lambda:'qq',get_self_id=lambda:'90000',get_group_id=lambda:'',get_sender_id=lambda:'12345678',get_message_str=lambda:'输入',get_extra=lambda k,d=None:extra.get(k,d),set_extra=lambda k,v:extra.update({k:v}))
        runtime['manage']('qq',{'action':'list'});st=runtime['stores']['qq'];work=st.pipeline.bind(event,lease)
        return event,st,work
    async def dispatch(event,send):
        token=current_message_event.set(event)
        try:await AiocqhttpMessageEvent._dispatch_send(NS(send_private_msg=send),None,False,'12345678',[{'type':'text','data':{'text':'草稿'}}])
        finally:current_message_event.reset(token)
    async def rejected(**kw):raise PreDispatchRejected('destination_changed_before_dispatch')
    try:
        event,st,work=fresh('reject-single')
        try:await dispatch(event,rejected)
        except PreDispatchRejected:pass
        else:raise AssertionError('rejection swallowed')
        assert (work.attempted,work.confirmed)==(0,0)
        assert st.db.execute('SELECT attempted FROM pipeline_events').fetchone()==(0,)

        event,st,work=fresh('reject-tail');st.pipeline.sending(work);st.pipeline.confirmed(work);work.confirmed_messages=1;work.planned_messages=2
        try:await dispatch(event,rejected)
        except PreDispatchRejected:pass
        st.pipeline.finish(work,{})
        assert (work.attempted,work.confirmed,work.status)==(1,1,'partial')

        event,st,work=fresh('newer-attempt');entered=asyncio.Event();release=asyncio.Event()
        async def late_rejected(**kw):entered.set();await release.wait();raise PreDispatchRejected('old_attempt')
        task=asyncio.create_task(dispatch(event,late_rejected));await entered.wait()
        st.pipeline.confirmed(work);st.pipeline.sending(work);release.set()
        try:await task
        except PreDispatchRejected:pass
        assert (work.attempted,work.confirmed)==(2,1),'old rejection removed a newer attempt'

        event,st,work=fresh('outer-has-no-attempt');st.pipeline.sending(work)
        try:await AiocqhttpMessageEvent.send(event,MessageChain(chain=[Plain('正常草稿')]))
        except PreDispatchRejected:pass
        else:raise AssertionError('outer rejection swallowed')
        st.pipeline.finish(work,{})
        assert (work.attempted,work.confirmed,work.status)==(1,0,'unknown'),'outer wrapper refunded an unowned attempt'

        event,st,work=fresh('timeout')
        async def timeout(**kw):raise asyncio.TimeoutError()
        try:await dispatch(event,timeout)
        except asyncio.TimeoutError:pass
        st.pipeline.finish(work,{})
        assert (work.attempted,work.confirmed,work.status)==(1,0,'unknown')
        print('PASS exact pre-dispatch attempt rollback, confirmed prefix, newer attempt, outer wrapper and timeout')
    finally:
        runtime['close']();temp.cleanup()


if __name__=='__main__':asyncio.run(run())
