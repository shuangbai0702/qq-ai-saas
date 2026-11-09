"""Lossless Journal storage codec; never changes provider message encoding.

Base is a native view, not necessarily a prefix of wire. References therefore
match the exact JSON (including object key order), with unmatched items inline.
Media references live outside user objects so arbitrary user keys cannot be
mistaken for codec instructions. Blobs contain the original UTF-8 string.
"""
import copy,hashlib,json

VERSION=2
MIN_MEDIA_CHARS=4096

def dumps(value):return json.dumps(value,ensure_ascii=False,separators=(',',':'))

def read_snapshot(db,scope):
    """Read a committed snapshot without creating tables or rewriting data."""
    row=db.execute('SELECT version,payload,sha FROM fishbot_readonly_journals WHERE scope=?',(scope,)).fetchone()
    if not row or row[1] is None:return None
    version,payload,sha=row
    if version not in (1,VERSION) or not isinstance(payload,str) or hashlib.sha256(payload.encode('utf-8')).hexdigest()!=sha:raise ValueError('journal_snapshot_integrity')
    def read_blob(key,digest):
        blob=db.execute('SELECT data FROM fishbot_readonly_journal_blobs WHERE scope=? AND sha=?',(key,digest)).fetchone()
        return blob[0] if blob else None
    value=json.loads(payload) if version==1 else decode(payload,scope,read_blob)
    if not isinstance(value,dict) or value.get('scope')!=scope or not isinstance(value.get('base'),list):raise ValueError('journal_snapshot_invalid')
    if any(not isinstance(m,dict) or not isinstance(m.get('role'),str) for m in value['base']):raise ValueError('journal_snapshot_invalid')
    return value

def encode(value):
    value=copy.deepcopy(value)
    indices={}
    for i,message in enumerate(value['wire']):indices.setdefault(dumps(message),i)
    value['base']=[{'wire':indices[dumps(m)]} if dumps(m) in indices else {'inline':m} for m in value['base']]
    refs=[];blobs={}
    def visit(node,path):
        if isinstance(node,str) and len(node)>=MIN_MEDIA_CHARS and node.startswith('data:') and node.split(',',1)[0].endswith(';base64'):
            raw=node.encode('utf-8');digest=hashlib.sha256(raw).hexdigest()
            blobs[digest]=raw;refs.append([path,digest,len(raw)])
            return None
        if isinstance(node,dict):return {k:visit(v,path+[k]) for k,v in node.items()}
        if isinstance(node,list):return [visit(v,path+[i]) for i,v in enumerate(node)]
        return node
    return dumps({'value':visit(value,[]),'media':refs}),blobs

def decode(payload,scope,read_blob):
    envelope=json.loads(payload)
    if not isinstance(envelope,dict) or set(envelope)!={'value','media'} or not isinstance(envelope['value'],dict) or not isinstance(envelope['media'],list):raise ValueError('journal_codec_shape')
    value=envelope['value']
    if value.get('scope')!=scope:raise ValueError('journal_codec_scope')
    seen=set()
    for ref in envelope['media']:
        if not isinstance(ref,list) or len(ref)!=3:raise ValueError('journal_blob_reference')
        path,digest,size=ref
        if not isinstance(path,list) or not path or any(type(k) not in (str,int) for k in path) or not isinstance(digest,str) or len(digest)!=64 or type(size) is not int or size<0:raise ValueError('journal_blob_reference')
        key=tuple(path)
        if key in seen:raise ValueError('journal_blob_duplicate')
        seen.add(key);parent=value
        for part in path[:-1]:parent=parent[part]
        if parent[path[-1]] is not None:raise ValueError('journal_blob_placeholder')
        raw=read_blob(scope,digest)
        if not isinstance(raw,bytes) or len(raw)!=size or hashlib.sha256(raw).hexdigest()!=digest:raise ValueError('journal_blob_integrity')
        parent[path[-1]]=raw.decode('utf-8')
    wire=value.get('wire');base=value.get('base')
    if not isinstance(wire,list) or not isinstance(base,list):raise ValueError('journal_codec_messages')
    native=[]
    for ref in base:
        if not isinstance(ref,dict):raise ValueError('journal_base_reference')
        if set(ref)=={'wire'}:
            index=ref['wire']
            if type(index) is not int or index<0 or index>=len(wire):raise ValueError('journal_base_index')
            native.append(copy.deepcopy(wire[index]))
        elif set(ref)=={'inline'}:native.append(ref['inline'])
        else:raise ValueError('journal_base_reference')
    value['base']=native
    return value
