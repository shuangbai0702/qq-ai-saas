"""Trusted auxiliary routes at the SDK boundary; offline, no paid calls."""
import ast,asyncio,contextvars,inspect,json,sys,unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'adapters/astrbot'))
import model_cache as m

PHASES=('private_freshness','group_freshness','group_presence','owner_contact','companion_plan')
def lease(bot='one',model='aux-model'):
    return {'tenantId':'tenant-'+bot,'botId':bot,'config':{'model':'main-model','auxiliaryModel':model}}
def event(owner):
    return NS(get_extra=lambda key,default=None:owner if key=='_hosted_lease' else default)

class RoutingTests(unittest.IsolatedAsyncioTestCase):
    def test_exact_five_phases_config_and_provider_binding(self):
        provider=NS();other=NS();e=event(lease())
        m.bind_system_contract(provider,object(),e.get_extra('_hosted_lease'))
        self.addCleanup(m.unbind_system_contract,provider)
        self.assertIsNone(m.auxiliary_model(e,provider))
        for phase in PHASES:
            with m.auxiliary_scope(provider,phase):
                self.assertEqual(m.auxiliary_model(e,provider),'aux-model')
                self.assertIsNone(m.auxiliary_model(e,other))
        for phase in ('foreground','inner_execution','planning','orientation','deliberation','summary','media_caption'):
            with m.auxiliary_scope(provider,phase):self.assertIsNone(m.auxiliary_model(e,provider))
        for model in (None,'','  ',123,'x'*151,' padded '):
            owner=lease(model=model);m.bind_system_contract(provider,object(),owner)
            with m.auxiliary_scope(provider,PHASES[0]):self.assertIsNone(m.auxiliary_model(event(owner),provider))

    def test_lingering_other_bot_event_cannot_select_its_config(self):
        a=lease('a','aux-a');b=lease('b','aux-b');provider=NS()
        with m.auxiliary_scope(provider,'group_presence'):
            self.assertIsNone(m.auxiliary_model(event(a),provider))
            m.bind_system_contract(provider,object(),b)
            try:
                self.assertIsNone(m.auxiliary_model(event(a),provider))
                self.assertIsNone(m.auxiliary_model(event({**b,'tenantId':'wrong-tenant'}),provider))
                self.assertEqual(m.auxiliary_model(event(b),provider),'aux-b')
                self.assertEqual(m.auxiliary_model(event({**b,'config':a['config']}),provider),'aux-b')
                with m.decision_scope(provider,a,'planning'):
                    self.assertIsNone(m.auxiliary_model(None,provider))
            finally:m.unbind_system_contract(provider)
            with m.decision_scope(provider,b,'planning'):
                self.assertIsNone(m.auxiliary_model(event(a),provider))
                self.assertEqual(m.auxiliary_model(event(b),provider),'aux-b')

    async def test_nested_cancellation_and_concurrent_bot_leases_restore(self):
        provider=NS();a=lease('a','aux-a');b=lease('b','aux-b')
        ready=asyncio.Event();release=asyncio.Event()
        async def first():
            with m.auxiliary_scope(provider,'owner_contact'),m.decision_scope(provider,a,'planning'):
                ready.set();await release.wait()
                self.assertEqual(m.auxiliary_model(None,provider),'aux-a')
                with m.auxiliary_scope(provider,'summary'):self.assertIsNone(m.auxiliary_model(None,provider))
                try:
                    with m.auxiliary_scope(provider,'companion_plan'):raise asyncio.CancelledError()
                except asyncio.CancelledError:pass
                self.assertEqual(m.auxiliary_model(None,provider),'aux-a')
            self.assertIsNone(m.auxiliary_model(None,provider))
        async def second():
            await ready.wait()
            self.assertIsNone(m.auxiliary_model(None,provider))
            with m.auxiliary_scope(provider,'group_presence'),m.decision_scope(provider,b,'planning'):
                self.assertEqual(m.auxiliary_model(None,provider),'aux-b');release.set()
                await asyncio.sleep(0)
                self.assertEqual(m.auxiliary_model(None,provider),'aux-b')
        await asyncio.gather(first(),second())

    def test_background_live_binding_and_config_removal(self):
        provider=NS();owner=lease()
        m.bind_system_contract(provider,object(),owner)
        try:
            with m.auxiliary_scope(provider,'companion_plan'):
                self.assertEqual(m.auxiliary_model(None,provider),'aux-model')
                owner['config'].pop('auxiliaryModel')
                self.assertIsNone(m.auxiliary_model(None,provider))
        finally:m.unbind_system_contract(provider)

    async def test_real_payload_boundary_and_final_custom_body_merge(self):
        # Execute the actual installed wrapper functions, isolating unrelated
        # Journal machinery by stopping at the original SDK preparation call.
        source=Path(__file__).resolve().parents[1]/'adapters/astrbot/behavior.py'
        tree=ast.parse(source.read_text(encoding='utf-8'))
        functions={n.name:n for n in ast.walk(tree) if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef))}
        cv=contextvars.ContextVar('route-event',default=event(lease()))
        provider=NS(provider_config={'model':'main-model','custom_extra_body':{'model':'custom-main'}})
        m.bind_system_contract(provider,object(),cv.get().get_extra('_hosted_lease'))
        self.addCleanup(m.unbind_system_contract,provider)
        before=json.loads(json.dumps(provider.provider_config));received=[]
        class Prepared(Exception):pass
        async def prepare(self,prompt,model=None):received.append((prompt,model));raise Prepared()
        def overrides(self,payload,extra):extra.update(self.provider_config['custom_extra_body'])
        namespace={'inspect':inspect,'prepare':prepare,'current_message_event':cv,'provider_overrides':overrides,
                   'autonomy':NS(decision_token_limit=contextvars.ContextVar('route-limit',default=None))}
        module=ast.Module(body=[functions['prepare_chat_payload'],functions['decision_output_override']],type_ignores=[])
        exec(compile(module,str(source),'exec'),namespace)
        for phase in (None,*PHASES,'summary'):
            with m.auxiliary_scope(provider,phase):
                with self.assertRaises(Prepared):await namespace['prepare_chat_payload'](provider,'body','caller-main')
                expected='aux-model' if phase in PHASES else 'caller-main'
                self.assertEqual(received[-1],('body',expected))
                payload={'model':expected};extra={}
                namespace['decision_output_override'](provider,payload,extra)
                actual={**payload,**extra}
                self.assertEqual(actual['model'],'aux-model' if phase in PHASES else 'custom-main')
                token=m.begin_attempt(cv.get(),provider);rows=[]
                try:
                    with patch.object(m,'record_diagnostic',side_effect=lambda _,row:rows.append(row)):
                        m.observe_wire_request(NS(content=json.dumps({**actual,'messages':[]}).encode()))
                    self.assertEqual(rows[-1]['model'],actual['model'])
                finally:m.reset_attempt(token)
        self.assertEqual(provider.provider_config,before)

if __name__=='__main__':unittest.main(verbosity=2)
