"""Own a real OneBot input before SDK conversion starts its first await.

The original lives only in pipeline_work_inputs. Recovery uses the original
converter directly; it already owns that same durable input.
"""
from types import SimpleNamespace, MethodType
import inspect
import time


def bind_api_connection(bot):
    """A departing SDK socket may remove only its own API registration."""
    from quart import websocket

    def remove_owned(connection):
        socket = websocket._get_current_object()
        self_id = websocket.headers['X-Self-ID']
        # Reconnect can install the replacement before the old finally runs.
        # Keep the SDK's single routing map, without a parallel connection pool.
        if connection._wsr_api_clients.get(self_id) is socket:
            del connection._wsr_api_clients[self_id]

    bot._remove_wsr_api_client = MethodType(remove_owned, bot)


def admitted(raw, config):
    """One trusted QQ boundary for messages and notices, before conversion."""
    if not isinstance(raw, dict): return False
    if raw.get('post_type') == 'notice' and raw.get('notice_type') == 'group_msg_emoji_like': return False
    group = str(raw.get('group_id') or '')
    if not group: return True
    from shared_context import group_whitelist
    whitelist = group_whitelist(config)
    configured = config.get('groups', [])
    return group in whitelist and (not configured or group in configured)


def directed_poke(raw):
    return (isinstance(raw, dict) and raw.get('post_type') == 'notice'
        and raw.get('notice_type') == 'notify' and raw.get('sub_type') == 'poke'
        and bool(raw.get('user_id')) and bool(raw.get('self_id'))
        and str(raw.get('target_id')) == str(raw['self_id'])
        and str(raw['user_id']) != str(raw['self_id']))


class RawInput:
    def __init__(self, member, raw):
        self.member = member
        self.message_obj = SimpleNamespace(raw_message=raw,
            message_id=str(raw.get('message_id', '')), timestamp=time.time())

    def get_platform_id(self): return self.member
    def get_sender_id(self): return str(self.message_obj.raw_message.get('user_id', ''))
    def get_self_id(self): return str(self.message_obj.raw_message.get('self_id', ''))
    def get_group_id(self): return str(self.message_obj.raw_message.get('group_id', '') or '')
    def get_extra(self, key, default=None): return default


def bind(platform, acquire, allowed=lambda raw: True, quiescing=lambda: False, prepare=None):
    """Bind once per platform; no task pool, raw cache or second queue."""
    from aiocqhttp import CQHttp
    bot = getattr(platform, 'bot', None)
    if isinstance(bot, CQHttp):bind_api_connection(bot)
    original = platform.convert_message
    async def retained(raw):
        derived = await prepare(raw) if prepare else raw
        message = await original(derived)
        if message is not None:
            message.raw_message = raw
            message._hosted_ingress_retained = True
        return message
    platform.convert_retained_message = retained

    async def convert(raw):
        if not allowed(raw): return None
        # Notice/request conversion has no stable message id. Preserve its
        # existing adapter path instead of inventing a recovery identity.
        if raw.get('post_type', 'message') != 'message':
            return await original(raw)
        release = acquire(raw)
        # During local retirement a dedicated SQLite connection persists this
        # same input off-loop. SDK conversion still starts only after commit.
        if inspect.isawaitable(release):release=await release
        if release is None: return None
        try:
            if quiescing(): return None
            message = await retained(raw)
            if message is not None:
                message._hosted_ingress_retained = True
            return message
        finally:
            release()

    platform.convert_message = convert
