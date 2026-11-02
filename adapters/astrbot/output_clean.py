"""把回复里漏出来的内部标签删掉。只依赖标准库，方便单独测。

Source: zexuanw958-svg/dafeiyu-config-pack, commit
8c01b0361b8a4baff5be3d2bc67ed55108953086; MIT, see output_clean.LICENSE.

会删的：
- AstrBot 塞进用户消息的 <system_reminder>…</system_reminder>（发消息人的 QQ 号、昵称、时间）；
  没闭合的 <system_reminder> 从它删到这一行末尾。没带标签的「User ID: 数字, Nickname: …」也删。
- DeepSeek 原生工具调用标记 DSML：<｜DSML｜function_calls> 整块、零散的 <｜DSML｜invoke …>、</｜DSML｜parameter> 等。
- 老格式 <｜tool▁calls▁begin｜>…<｜tool▁calls▁end｜>、<tool_call>…</tool_call>。
"""
import copy
import re
from message_layout import without_boundaries

_BAR = r"[|｜]"
_PATTERNS = [
    re.compile(r"<\s*system_reminder\s*>.*?<\s*/\s*system_reminder\s*>", re.S | re.I),
    re.compile(r"<\s*system_reminder\s*>[^\n]*", re.I),
    re.compile(r"<\s*/\s*system_reminder\s*>", re.I),
    re.compile(rf"<\s*{_BAR}+\s*DSML\s*{_BAR}+\s*function_calls\s*>.*?(?:<\s*/\s*{_BAR}+\s*DSML\s*{_BAR}+\s*function_calls\s*>|$)", re.S | re.I),
    re.compile(rf"<\s*/?\s*{_BAR}+\s*DSML\s*{_BAR}+[^>]*>", re.I),
    re.compile(r"<｜tool▁calls▁begin｜>.*?(?:<｜tool▁calls▁end｜>|$)", re.S),
    re.compile(r"<｜[^｜>\n]*▁[^｜>\n]*｜>"),
    re.compile(r"<\s*tool_call\s*>.*?(?:<\s*/\s*tool_call\s*>|$)", re.S | re.I),
    re.compile(r"User ID:\s*\d+\s*,\s*Nickname:[^\n<]*"),
]
_HINT = re.compile(r"system_reminder|DSML|tool▁|tool_call|User ID:", re.I)
_SPACE = r'[\s\u200b-\u200d\ufeff]*'
_IMAGE_CONTROL = re.compile(r'\['+_SPACE+r'(?:(M'+_SPACE+'E'+_SPACE+'M'+_SPACE+'E)|(I'+_SPACE+'M'+_SPACE+'A'+_SPACE+'G'+_SPACE+'E))'+_SPACE+r':([^\]]{0,512})(?:\]|$)',re.I)


def normalize_image_markers(text):
    """Whitespace is presentation, never an alternative image identity.

    Catalog/candidate authorization still happens after normalization. Invalid
    control payloads disappear instead of becoming public assistant prose.
    """
    def replace(match):
        identifier=re.sub(r'[\s\u200b-\u200d\ufeff]+','',match[3])
        if not re.fullmatch(r'[a-z0-9_-]{1,32}',identifier):return ''
        return '['+('MEME' if match[1] else 'IMAGE')+':'+identifier+']'
    return _IMAGE_CONTROL.sub(replace,text)


def without_image_markers(text):
    return _IMAGE_CONTROL.sub('',text)


def dirty(text: str) -> bool:
    return bool(text) and bool(_HINT.search(text))


def clean(text: str) -> str:
    if not dirty(text):
        return text
    for p in _PATTERNS:
        text = p.sub("", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def prepare_history(messages: list) -> list:
    """Remove leaked controls on a private copy, preserving dialogue paragraphs."""
    result = copy.deepcopy(messages)
    for message in result:
        if not isinstance(message, dict) or message.get('role') != 'assistant' or not isinstance(message.get('content'), str):
            continue
        text = without_image_markers(without_boundaries(clean(message['content'])))
        message['content'] = text
    return result
