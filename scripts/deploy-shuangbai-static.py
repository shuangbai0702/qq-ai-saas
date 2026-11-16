"""Deploy public static assets while preserving a Worker's existing API proxy.
Credentials: CF_API_EMAIL / CF_API_KEY. Never upload runtime data or private files.
Uses Cloudflare's documented direct-upload manifest, bucket and completion-token flow.
"""
import argparse,base64,email,email.policy,hashlib,json,mimetypes,os
from pathlib import Path
import requests

def main():
    parser=argparse.ArgumentParser()
    for name in ('account','worker','dist','source','headers','state-dir'):parser.add_argument('--'+name,required=True)
    parser.add_argument('--expected-source');parser.add_argument('--upstream-origin')
    args=parser.parse_args();state=Path(args.state_dir);state.mkdir(parents=True,exist_ok=True)
    session=requests.Session();session.headers.update({'X-Auth-Email':os.environ['CF_API_EMAIL'],'X-Auth-Key':os.environ['CF_API_KEY']})
    base='https://api.cloudflare.com/client/v4/accounts/'+args.account;script=base+'/workers/scripts/'+args.worker
    def call(method,url,**kwargs):
        response=session.request(method,url,timeout=60,**kwargs)
        data=response.json()
        if not response.ok or not data.get('success'):raise RuntimeError('Cloudflare request failed: '+str(response.status_code)+' '+json.dumps(data.get('errors')))
        return data['result']
    current=session.get(script,timeout=30)
    settings={}
    if current.ok:
        (state/'worker-before.raw').write_bytes(current.content)
        message=email.message_from_bytes(('Content-Type: '+current.headers['Content-Type']+'\r\nMIME-Version: 1.0\r\n\r\n').encode()+current.content,policy=email.policy.default)
        modules={part.get_param('name',header='content-disposition'):part.get_payload(decode=True) for part in message.iter_parts()}
        expected=Path(args.expected_source or args.source).read_bytes()
        if len(modules)!=1 or next(iter(modules.values()))!=expected:raise RuntimeError('Live proxy differs from supplied source; inspect before deployment')
        settings=call('GET',script+'/settings');(state/'settings-before.json').write_text(json.dumps(settings,indent=2))
        deployments=call('GET',script+'/deployments');(state/'deployments-before.json').write_text(json.dumps(deployments,indent=2))
    elif current.status_code!=404:raise RuntimeError('Unable to back up Worker: '+str(current.status_code))
    manifest={};files={}
    for file in sorted(Path(args.dist).rglob('*')):
        if not file.is_file():continue
        name='/'+file.relative_to(args.dist).as_posix()
        if not (name in ('/index.html','/boot.js') or name.startswith(('/assets/','/illustrations/','/paper/','/voices/'))):raise ValueError('Unexpected public asset: '+name)
        raw=file.read_bytes();encoded=base64.b64encode(raw);digest=hashlib.sha256(encoded+file.suffix[1:].encode()).hexdigest()[:32]
        manifest[name]={'hash':digest,'size':len(raw)};files[digest]=(file,encoded)
    (state/'manifest.json').write_text(json.dumps(manifest,indent=2))
    upload=call('POST',script+'/assets-upload-session',json={'manifest':manifest});jwt=upload['jwt'];completion=jwt if not upload['buckets'] else None
    for i,bucket in enumerate(upload['buckets']):
        payload={digest:(digest,files[digest][1],mimetypes.guess_type(files[digest][0].name)[0] or 'application/octet-stream') for digest in bucket}
        response=requests.post(base+'/workers/assets/upload?base64=true',headers={'Authorization':'Bearer '+jwt},files=payload,proxies=session.proxies or None,timeout=60)
        result=response.json()
        if not response.ok or not result.get('success'):raise RuntimeError('Asset upload failed: '+str(response.status_code)+' '+json.dumps(result.get('errors')))
        completion=result.get('result',{}).get('jwt') or completion
        print('Uploaded bucket',i+1,'of',len(upload['buckets']),flush=True)
    if not completion:raise RuntimeError('Missing upload completion token')
    metadata={key:settings[key] for key in ('compatibility_date','compatibility_flags','usage_model','bindings','tail_consumers','logpush','placement','tags') if key in settings}
    if args.upstream_origin:
        from urllib.parse import urlsplit
        origin=urlsplit(args.upstream_origin)
        if origin.scheme!='https' or not origin.hostname or origin.username or origin.password or origin.path not in ('','/') or origin.query or origin.fragment:raise ValueError('Invalid fixed upstream origin')
        metadata['bindings']=[binding for binding in metadata.get('bindings',[]) if binding['name']!='UPSTREAM_ORIGIN']+[{'name':'UPSTREAM_ORIGIN','type':'plain_text','text':args.upstream_origin.rstrip('/')} ]
    metadata.update(main_module='worker.js',compatibility_date=settings.get('compatibility_date','2024-11-01'),assets={'jwt':completion,'config':{'html_handling':'auto-trailing-slash','not_found_handling':'none','run_worker_first':False,'_headers':Path(args.headers).read_text(encoding='utf-8-sig')}})
    result=call('PUT',script,files={'metadata':('metadata.json',json.dumps(metadata),'application/json'),'worker.js':('worker.js',Path(args.source).read_bytes(),'application/javascript+module')})
    # Do not persist JWTs or credentials in the deployment evidence.
    (state/'result.json').write_text(json.dumps({'worker':args.worker,'result':result,'files':len(manifest),'sourceSha256':hashlib.sha256(Path(args.source).read_bytes()).hexdigest()},indent=2))
    print(json.dumps({'deployed':args.worker,'version':result.get('version_id'),'files':len(manifest)}))
if __name__=='__main__':main()
