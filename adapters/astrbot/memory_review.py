"""Deterministic compression-only pages, never a store or runtime context."""
import copy,json

PAGE_CHARS=24000


def pages(state,budget=PAGE_CHARS):
    """Keep an entire fact CAS group together; cover every offered candidate."""
    def size(value):return len(json.dumps(value,ensure_ascii=False,separators=(',',':')))
    if size(state)<=budget:return [state]
    base={k:copy.deepcopy(state[k]) for k in ('scope','subjects','writable')}
    def empty():return {**base,'facts':[],'relations':[],'products':{k:[] for k in state.get('products',{})},'fact_versions':[],'relation_versions':[]}
    groups=[]
    for version in state['fact_versions']:
        group=[r for r in state['facts'] if (r['subject'],r['name'])==(version['subject'],version['name'])]
        groups.append(('facts',group,version))
    for row in state['relations']:
        version=next(r for r in state['relation_versions'] if r['subject']==row['subject'])
        groups.append(('relations',[row],version))
    for kind,rows in sorted(state.get('products',{}).items()):
        groups.extend((kind,[row],None) for row in sorted(rows,key=lambda r:json.dumps(r['target'],sort_keys=True)))
    result=[];current=empty()
    def add(page,kind,rows,version):
        target=page[kind] if kind in ('facts','relations') else page['products'][kind]
        target.extend(rows)
        if version is not None:page['fact_versions' if kind=='facts' else 'relation_versions'].append(version)
    for kind,rows,version in groups:
        candidate=copy.deepcopy(current);add(candidate,kind,rows,version)
        if size(candidate)>budget:
            if current!=empty():result.append(current)
            candidate=empty();add(candidate,kind,rows,version)
            if size(candidate)>budget:raise ValueError('semantic_review_single_candidate_capacity')
        current=candidate
    if current!=empty():result.append(current)
    return result or [empty()]


def coverage(state):
    return {'facts':len(state['facts']),'relations':len(state['relations']),
            'products':sum(len(rows) for rows in state.get('products',{}).values())}


def reject(rejections,key,reason):
    if rejections is not None:
        counts=rejections.setdefault(key,{})
        counts[reason]=counts.get(reason,0)+1


def combine(results,rejections=None):
    """Reject conflicting optional edits locally; never guess a winning value."""
    merged={key:[] for key in ('fact_operations','relation_operations','memory_operations')};seen={}
    conflicts=set()
    for data in results:
        for key in merged:
            operations=data.get(key,[])
            if not isinstance(operations,list):reject(rejections,key,'invalid_container');continue
            for op in operations:
                if not isinstance(op,dict):reject(rejections,key,'invalid_operation');continue
                identity=(key,json.dumps(op.get('target') if key=='memory_operations' else [op.get('subject'),op.get('name') if key=='fact_operations' else ''],sort_keys=True))
                if identity in conflicts:reject(rejections,key,'conflicting_operations');continue
                if identity in seen:
                    if seen[identity]!=op:
                        merged[key].remove(seen[identity]);conflicts.add(identity)
                        reject(rejections,key,'conflicting_operations')
                    continue
                seen[identity]=op;merged[key].append(op)
    return merged


def validate_page(data,page,rejections=None):
    """Narrow page authority without letting an optional bad edit veto the cut."""
    for key,versions in (('fact_operations',page['fact_versions']),('relation_operations',page['relation_versions'])):
        rows=data.get(key,[])
        valid=[]
        if not isinstance(rows,list):reject(rejections,key,'invalid_container');rows=[]
        for op in rows:
            if not isinstance(op,dict):reject(rejections,key,'invalid_operation');continue
            if op.get('op') not in ('replace','delete') or op.get('scope')!=page['scope']:reject(rejections,key,'operation_not_offered');continue
            match=next((v for v in versions if v['subject']==op.get('subject') and (key!='fact_operations' or v['name']==op.get('name'))),None)
            if not match or op.get('expected_version')!=match['version']:reject(rejections,key,'operation_not_offered');continue
            valid.append(op)
        if key in data:data[key]=valid
    rows=data.get('memory_operations',[])
    valid=[]
    if not isinstance(rows,list):reject(rejections,'memory_operations','invalid_container');rows=[]
    for op in rows:
        if not isinstance(op,dict):reject(rejections,'memory_operations','invalid_operation');continue
        if op.get('op') not in ('replace','delete') or not isinstance(op.get('kind'),str):reject(rejections,'memory_operations','operation_not_offered');continue
        match=next((r for r in page['products'].get(op.get('kind'),[]) if r['target']==op.get('target')),None)
        if not match or op.get('expected_version')!=match['version']:reject(rejections,'memory_operations','operation_not_offered');continue
        valid.append(op)
    if 'memory_operations' in data:data['memory_operations']=valid
