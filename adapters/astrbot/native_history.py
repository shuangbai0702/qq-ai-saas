"""Count hosted history in complete user turns, not tool message pairs.

Only the owned runner's turn limiter is replaced. Native token pressure,
compression, summary coverage and persisted history remain authoritative.
"""


class LogicalTurnLimiter:
    def __init__(self, native):
        self.native = native

    def __getattr__(self, name):
        return getattr(self.native, name)

    def truncate_by_turns(self, messages, keep_most_recent_turns, drop_turns=1):
        if keep_most_recent_turns == -1:
            return messages
        # Unusual upstream inputs retain native behavior; hosted configuration
        # uses positive integers. Do not guess boundaries for orphaned tools.
        if type(keep_most_recent_turns) is not int or keep_most_recent_turns < 1 or type(drop_turns) is not int or drop_turns < 1:
            return self.native.truncate_by_turns(messages, keep_most_recent_turns, drop_turns)
        first = next((i for i, message in enumerate(messages) if message.role != 'system'), len(messages))
        rest = messages[first:]
        if not rest:
            return messages
        if rest[0].role != 'user':
            return self.native.truncate_by_turns(messages, keep_most_recent_turns, drop_turns)
        starts = [i for i, message in enumerate(rest) if message.role == 'user']
        if len(starts) <= keep_most_recent_turns:
            return messages
        keep = max(1, keep_most_recent_turns - drop_turns + 1)
        # A complete turn includes every tool call/result and the final answer.
        # No trimming within a turn, and no resurrection of the first user.
        return messages[:first] + rest[starts[-keep]:]


def bind_history_rounds(runner):
    """Per-runner binding after native reset; never monkey-patch other tenants."""
    manager = runner.request_context_manager
    if not isinstance(manager.truncator, LogicalTurnLimiter):
        manager.truncator = LogicalTurnLimiter(manager.truncator)
