"""Own a real OneBot input before SDK conversion starts its first await.

The original lives only in pipeline_work_inputs. Recovery uses the original
converter directly; it already owns that same durable input.
"""
from types import SimpleNamespace
import time


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


def bind(platform, acquire, allowed=lambda raw: True, quiescing=lambda: False):
    """Bind once per platform; no task pool, raw cache or second queue."""
    original = platform.convert_message
    platform.convert_retained_message = original

    async def convert(raw):
        # Notice/request conversion has no stable message id. Preserve its
        # existing adapter path instead of inventing a recovery identity.
        if raw.get('post_type', 'message') != 'message':
            return await original(raw)
        if not allowed(raw): return None
        release = acquire(raw)  # synchronous persistence before any await
        if release is None: return None
        try:
            if quiescing(): return None
            message = await original(raw)
            if message is not None:
                message._hosted_ingress_retained = True
            return message
        finally:
            release()

    platform.convert_message = convert
