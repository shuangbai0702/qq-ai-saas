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


def combine(results):
    """Identical creates can recur on different pages; conflicts never guess."""
    merged={key:[] for key in ('fact_operations','relation_operations','memory_operations')};seen={}
    for data in results:
        for key in merged:
            operations=data.get(key,[])
            if not isinstance(operations,list):raise ValueError('semantic_review_invalid_operations')
            for op in operations:
                if not isinstance(op,dict):raise ValueError('semantic_review_invalid_operation')
                identity=(key,json.dumps(op.get('target'),sort_keys=True)) if key=='memory_operations' else (key,op.get('subject'),op.get('name') if key=='fact_operations' else '')
                if identity in seen:
                    if seen[identity]!=op:raise ValueError('semantic_review_conflicting_operations')
                    continue
                seen[identity]=op;merged[key].append(op)
    return merged


def validate_page(data,page):
    """Page admission is narrower than the later transaction's full snapshot."""
    for key,versions in (('fact_operations',page['fact_versions']),('relation_operations',page['relation_versions'])):
        rows=data.get(key,[])
        if not isinstance(rows,list):raise ValueError('semantic_review_invalid_operations')
        for op in rows:
            if not isinstance(op,dict) or op.get('op') not in ('replace','delete') or op.get('scope')!=page['scope']:raise ValueError('semantic_review_operation_not_offered')
            match=next((v for v in versions if v['subject']==op.get('subject') and (key!='fact_operations' or v['name']==op.get('name'))),None)
            if not match or op.get('expected_version')!=match['version']:raise ValueError('semantic_review_operation_not_offered')
    rows=data.get('memory_operations',[])
    if not isinstance(rows,list):raise ValueError('semantic_review_invalid_operations')
    for op in rows:
        if not isinstance(op,dict) or op.get('op') not in ('replace','delete'):raise ValueError('semantic_review_operation_not_offered')
        match=next((r for r in page['products'].get(op.get('kind'),[]) if r['target']==op.get('target')),None)
        if not match or op.get('expected_version')!=match['version']:raise ValueError('semantic_review_operation_not_offered')
