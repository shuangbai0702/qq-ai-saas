"""Checkpoint-owned conversational bridge, never raw turns or an execution ledger.

The existing summary transaction owns writes. Journal adoption retires the old
product; reads have no clock, quota, model call or mutation side effects.
"""
import json,re
from context_protocol import model_json
from memory_policy import CONTINUITY_MAX_CHARS

SYSTEM=(
    '本次同一个JSON对象另外输出可选conversation_continuity数组，每项为'
    '{"source_scope":"本批可信来源会话","text":"近期交流连续性摘要"}；无必要线索可输出null。'
    f'全部text合计尽量控制在{CONTINUITY_MAX_CHARS}字以内，程序按最多{CONTINUITY_MAX_CHARS}个Unicode字符接收。'
    '这是交给下一段对话的接话便笺，不是长期摘要的缩写或待办清单。'
    '从本批末段的真实用户和机器人问答自主选择近期话题、关键指代、称谓、当下语气及未结束内容，不固定最近几轮。'
    '写清交流停在哪里、双方最近具体说过什么和已答过什么；有助于接话的独特说法可保留短原话。'
    '最近已回答的轻松互动也有接话价值，不只记录尚未执行的事项；不要仅剩主题标签或笼统的“继续交流”。'
    '前文省略普通闲聊、短期情绪的规则仅适用于长期summary和长期认知产品，不适用于本字段。'
    'summary仍整理全部批次的长期认知；本字段优先保存近期交流细节，避免挤入无关的远期计划或早已结束的话题。'
    '不复制完整对话、长期事实清单、任务账本、工具JSON、原始事件ID或图片地址；'
    '与summary可有理解接话所必需的简短重叠，但不能只是换句话复述summary。'
    '只记真实可见的交流线索，缺失不等于此前未发生，不能编造消息、作者、约定或执行结果。'
    '每个来源会话单独一项，只用continuity_sources内的source_scope，不混淆群私、人物或原始作者。'
    '这是本批重新生成的交流桥，不读取或续写旧连续性摘要，不授予权限，不创建任务；'
    '新真实交流始终优先，不强迫继续旧话题，也不制定回复模板。'
)

MEANING='合法检查点提炼的近期交流线索，不是真实新发言、长期事实或执行凭据；按来源理解，新真实交流优先，不强迫续聊旧话题。只在下一合法检查点替换，不按时间或轮数过期。'
_SECRET=re.compile(r'(?i)\bsk-[A-Za-z0-9_-]{8,}|Bearer\s+\S+')


def source_scopes(scope,authors):
    """Use verified witnesses, never an ID inferred from model prose."""
    from memory_scope import valid_scope
    result=set()
    for row in authors:
        origin=row.get('source') if isinstance(row,dict) else None
        channel=origin.get('source_scope') if isinstance(origin,dict) else None
        if valid_scope(channel) and channel.startswith(('private:','group:')) and (scope=='bot_shared' or channel==scope):result.add(channel)
    return sorted(result)


def accept(data,scope,authors,audit=None):
    """Reject only this optional product; DB/summary failures remain failures."""
    if 'conversation_continuity' not in data:
        if audit:
            audit.product('conversation_continuity','accepted','optional_continuity_not_provided')
            audit.counts['conversation_continuity']['omitted']=True
        return None
    value=data['conversation_continuity'];allowed=source_scopes(scope,authors)
    def receipt(decision,reason,changed=False):
        if audit:audit.product('conversation_continuity',decision,reason,changed=changed)
    if value is None or value==[]:
        receipt('accepted','no_continuity_selected');return None
    # A plain paragraph is unambiguous only for one verified source scope.
    if isinstance(value,str) and len(allowed)==1:value=[{'source_scope':allowed[0],'text':value}]
    if not isinstance(value,list):receipt('rejected','invalid_continuity_container');return None
    rows=[];seen=set();characters=0
    for item in value:
        if not isinstance(item,dict) or set(item)!={'source_scope','text'}:
            receipt('rejected','invalid_continuity_item');return None
        channel=item['source_scope'];text=item['text']
        if not isinstance(channel,str) or channel not in allowed or channel in seen:
            receipt('rejected','continuity_scope_not_offered_or_duplicate');return None
        if not isinstance(text,str) or not text.strip() or '\x00' in text or _SECRET.search(text):
            receipt('rejected','invalid_or_sensitive_continuity_text');return None
        text=text.strip();characters+=len(text)
        if characters>CONTINUITY_MAX_CHARS:
            receipt('rejected','continuity_capacity_exceeded');return None
        rows.append({'source_scope':channel,'text':text});seen.add(channel)
    receipt('accepted','checkpoint_product_saved',changed=bool(rows))
    return model_json(rows) if rows else None


def _has_column(db):
    return 'conversation_continuity' in {r[1] for r in db.execute('PRAGMA table_info(memory_reconstruction_checkpoints)')}


def view(db,scope,checkpoint_id=None):
    """Read the exact newest legal product; NULL never falls back to an old one."""
    if not _has_column(db):return None
    from summary_material import checkpoint_round_gate
    tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not {'memory_reflection_control','memory_semantic_batches'}<=tables:return None
    where=' AND c.id=?' if checkpoint_id is not None else ''
    args=(scope,checkpoint_id) if checkpoint_id is not None else (scope,)
    row=db.execute(f'''SELECT c.id,c.conversation_continuity FROM memory_reconstruction_checkpoints c
      JOIN memory_semantic_batches b ON b.checkpoint_id=c.id AND b.scope=c.scope
      WHERE c.scope=? AND c.epoch=(SELECT epoch FROM memory_reflection_control WHERE id=1)
      AND c.state IN ('available','reserved','consumed') AND b.state IN ('committed','retired')
      AND {checkpoint_round_gate(db)}{where} ORDER BY c.through_round_id DESC,c.id DESC LIMIT 1''',args).fetchone()
    if not row or row[1] is None:return None
    try:items=json.loads(row[1])
    except (ValueError,TypeError):return None
    # Storage is not a source of new authority. Validate old/corrupt rows too.
    from memory_scope import valid_scope
    if not isinstance(items,list) or not items:return None
    seen=set();size=0
    for item in items:
        if not isinstance(item,dict) or set(item)!={'source_scope','text'}:return None
        channel=item['source_scope'];text=item['text']
        if not valid_scope(channel) or not channel.startswith(('private:','group:')) or (scope!='bot_shared' and channel!=scope) or channel in seen:return None
        if not isinstance(text,str) or not text.strip() or '\x00' in text or _SECRET.search(text):return None
        size+=len(text);seen.add(channel)
    if size>CONTINUITY_MAX_CHARS:return None
    return {'checkpoint_id':row[0],'items':items,'meaning':MEANING}


def retire_previous(db,scope,checkpoint_id):
    """Caller owns Journal adoption transaction. Keep a newer staged product."""
    if not _has_column(db):return
    row=db.execute('SELECT through_round_id,epoch FROM memory_reconstruction_checkpoints WHERE id=? AND scope=?',(checkpoint_id,scope)).fetchone()
    if not row:return
    db.execute('''UPDATE memory_reconstruction_checkpoints SET conversation_continuity=NULL
      WHERE scope=? AND id<>? AND (epoch<>? OR through_round_id<=?)''',(scope,checkpoint_id,row[1],row[0]))
