"""Last-resort, explicitly lossy products for a due formal checkpoint.

No provider, alternate history store, fabricated fact or delivery receipt. The
normal checkpoint transaction and Journal writer still own commit/retirement.
"""
import json
from memory_policy import origin,scrub
NORMAL_TIMEOUT_SECONDS=360


def reconstruction(state):
    """Small cognitive view at a legal cut; persistent products stay owned.

    Omitted products can be read again through their normal query/delta entry.
    System, Persona, character cards and unretired physical blocks are untouched.
    """
    foundation=state.get('memory_foundation') or {}
    kept={k:foundation[k] for k in ('conversation_summaries','core_note','conversation_continuity') if k in foundation}
    result={'memory_foundation':kept,'compression_visibility':{'partial':True,'reason':'checkpoint_capacity','meaning':'本次检查点仅装入摘要及核心记忆；其他持久认知未删除，按需查询。'}}
    if 'foundation_core_note' in state:result['foundation_core_note']=state['foundation_core_note']
    return result


def context(db,scope,rows):
    """Fence each real completed input, even when semantic preparation failed.

    Unreadable/conflicting material is declared missing, never made complete.
    Only these exact members acquire retirement permission; unrelated or owned
    inputs retain their existing responsibility and cannot be swept away.
    """
    from semantic_batch import load
    from semantic_collection import unavailable_stamp
    materials={};discarded=[]
    for row in rows:
        item={'event':row[2],'sender_id':row[1]}
        try:
            captures,_,_=load(db,scope,[item])
            materials.update(captures)
        except (ValueError,TypeError,KeyError,json.JSONDecodeError):
            channel,_=origin(scope,row[2])
            discarded.append({'event':row[2],'source_scope':channel,'sender_ids':[],
                              'missing':'incomplete_original','available_text':[],
                              'available_reply':[],'available_observations':[],'images':[]})
    return {'previous_products':[],'previous_dependency':None,'legacy_product_ids':[],
            'payload':[],'author_payload':[],'before':None,'materials':materials,
            'learning_batches':[],'learning_epoch':None,'prior_claims':[],
            'discarded':discarded,'discard_stamp':unavailable_stamp(db,discarded,scope=scope) if discarded else None,
            'degraded':True}


def product(rows,*,big=False):
    if big:
        # These are already semantic products, not retired raw dialogue. Keep
        # balanced excerpts of each source, and acknowledge the omitted tail.
        budget=max(1,(1400-len(rows)*2)//max(1,len(rows)))
        text='历史摘要合并降级，部分细节未保留。'+ '；'.join(scrub(str(r[1]))[:budget] for r in rows)
        return {'summary':text[:1500]}
    return {'summary':f'本批{len(rows)}个已完成业务轮压缩发生故障，已执行有损检查点；本批新增经历未完成语义提炼，细节未保留。既有认知保持原状；外部操作是否完成仍以真实回执账本为准。'}
