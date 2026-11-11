"""Lossless reading representation, used only when emitting NEW model data.

Storage, CAS snapshots, selection budgets and committed wire are not inputs to
this codec. Only explicitly owned memory containers are traversed.
"""
import copy
from context_protocol import model_json

FORMAT='FishBot.memory_rows.v1'
MEANING='只读对象数组：每行按columns对应字段，再合并common还原原记录；保留行序和值。先还原，再应用readonlyChanges/eventChanges原字段路径；不是新授权。'
LISTS=frozenset(('facts','relations','protected_claims','previous_commitments'))
OBJECTS=frozenset(('state','memory_foundation','persona_core_note','core_note',
    'memory','owner_memory','reference','changes','existing_memory'))
SUMMARIES=frozenset(('conversation_summaries','previous_summaries'))


def rows_view(rows):
    # Mixed/unknown records keep their original shape. In particular, absence
    # must never become null, a default, or an inherited value.
    if not isinstance(rows,list) or len(rows)<2 or not all(isinstance(r,dict) for r in rows):return rows
    keys=list(rows[0])
    if not keys or any(set(r)!=set(keys) for r in rows):return rows
    common={k:rows[0][k] for k in keys if all(model_json(r[k])==model_json(rows[0][k]) for r in rows[1:])}
    columns=[k for k in keys if k not in common]
    result={'format':FORMAT,'meaning':MEANING,'common':common,'columns':columns,
            'rows':[[r[k] for k in columns] for r in rows]}
    return result if len(model_json(result))<len(model_json(rows)) else rows


def memory_view(value):
    """Private copy; never interpret keys inside prose, tools or receipts."""
    result=copy.deepcopy(value)
    def visit(obj):
        if not isinstance(obj,dict):return
        for key,item in list(obj.items()):
            if key in LISTS:obj[key]=rows_view(item)
            elif key in OBJECTS:visit(item)
            elif key in SUMMARIES and isinstance(item,list):
                for summary in item:visit(summary)
        # The event authority keeps its role and all identity/permission data.
        # Only its explicitly owned core-note memory is a reading projection.
        authority=obj.get('authority')
        if isinstance(authority,dict):visit(authority.get('persona_core_note'))
        # These set values retain their *logical* object/list shape. The
        # self-describing table explicitly expands before path application.
        for name in ('readonlyChanges','eventChanges'):
            for op in obj.get(name,[]) if isinstance(obj.get(name),list) else []:
                if not isinstance(op,list) or len(op)!=3 or op[0]!='set' or not isinstance(op[1],list):continue
                target=op[1][-1] if op[1] else ''
                if not isinstance(target,(str,int)):continue
                if target in LISTS:op[2]=rows_view(op[2])
                elif not op[1] or target in OBJECTS:visit(op[2])
                elif target=='authority' and isinstance(op[2],dict):visit(op[2].get('persona_core_note'))
    visit(result)
    return result


def memory_json(value):
    return model_json(memory_view(value))
