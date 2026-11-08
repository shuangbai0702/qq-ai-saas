"""把回复里漏出来的内部标签删掉。只依赖标准库，方便单独测。

Source: zexuanw958-svg/dafeiyu-config-pack, commit
8c01b0361b8a4baff5be3d2bc67ed55108953086; MIT, see output_clean.LICENSE.

会删的：
- AstrBot 塞进用户消息的 <system_reminder>…</system_reminder>（发消息人的 QQ 号、昵称、时间）；
  没闭合的 <system_reminder> 从它删到这一行末尾。没带标签的「User ID: 数字, Nickname: …」也删。
- DeepSeek 原生工具调用标记 DSML：<｜DSML｜function_calls> 整块、零散的 <｜DSML｜invoke …>、</｜DSML｜parameter> 等。
- 老格式 <｜tool▁calls▁begin｜>…<｜tool▁calls▁end｜>、<tool_call>…</tool_call>。

只隔离新响应里的明确推理控制块（含未闭合变体），不根据普通正文猜测推理。
代码示例保留；已提交历史只深拷贝，绝不通过清洗重写。
"""
import copy
import re
from message_layout import without_boundaries, normalize_chat_layout
from reply_voice import without_markers as without_voice_markers

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

# Explicit protocol markup only; prose, speaker intent and ordinary headings
# are never classified as reasoning. Quoted code remains ordinary answer data.
_REASON_START=re.compile(r'<\s*(/?)\s*(?:thinking|think)(?=>|\s|[^\x00-\x7f]|$)',re.I)
_CODE=re.compile(r'(`{3,}|~{3,})[^\n]*\n.*?(?:\n\1[^\n]*(?:\n|$)|$)|(`+)[^`\n]*?\2',re.S)
_QUOTED_CONTROL=re.compile(r'''["'“‘「『]<\s*/?\s*(?:thinking|think)\s*>["'”’」』]''',re.I)

def without_analysis(text: str) -> str:
    """Strip reserved reasoning blocks in NEW output, not committed history.

    Unterminated open blocks withhold the remainder; an orphan closing control
    removes only itself. No attempt to guess a hidden answer inside a block.
    """
    if not isinstance(text,str) or not _REASON_START.search(text):return text
    protected=[(m.start(),m.end()) for pattern in (_CODE,_QUOTED_CONTROL) for m in pattern.finditer(text)]
    result=[];cursor=0;depth=0
    for match in _REASON_START.finditer(text):
        if any(start<=match.start()<end for start,end in protected):continue
        if match.start()<cursor:continue
        end=text.find('>',match.end());next_open=text.find('<',match.end())
        # Missing '>' is a malformed reserved opening, not an ordinary tag.
        token_end=end+1 if end>=0 and (next_open<0 or end<next_open) else match.end()
        if not depth:result.append(text[cursor:match.start()])
        if match[1]:depth=max(0,depth-1)
        else:depth+=1
        cursor=token_end
    if not depth:result.append(text[cursor:])
    return ''.join(result)


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
    text = without_analysis(normalize_chat_layout(text))
    if not text or not dirty(text):
        return text
    for p in _PATTERNS:
        text = p.sub("", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def prepare_history(messages: list) -> list:
    """History is immutable. New outputs are sanitized before SDK admission."""
    return copy.deepcopy(messages)


def memory_projection(prompt):
    """Exclude explicit bot reasoning from a NEW summarizer input only.

    Preserve human quotations, tools, raw evidence, hashes and coverage. This
    view is never persisted back into the authoritative conversation sources.
    """
    result=copy.deepcopy(prompt)
    for row in result.get('turns',[]):
        if isinstance(row,dict) and isinstance(row.get('bot'),str):row['bot']=without_analysis(row['bot'])
    for row in result.get('sent_messages',[]):
        if isinstance(row,dict) and row.get('role')=='self' and isinstance(row.get('text'),str):row['text']=without_analysis(row['text'])
    for row in result.get('observed_requests',[]):
        if not isinstance(row,dict):continue
        for key in ('confirmed_reply','native_reply'):
            if isinstance(row.get(key),str):row[key]=without_analysis(row[key])
    return result
