"""Bounded synthetic prompt comparison. Never executes returned tool calls.

Inputs are captured synthetic request bodies, not production conversations.
The credential is inherited through FISHBOT_EVAL_API_KEY only. Every attempted
completion, including failures, consumes the durable 64-request ceiling.
"""
import argparse,asyncio,hashlib,json,os,time
from pathlib import Path
import httpx


async def run(args):
    key=os.environ.get('FISHBOT_EVAL_API_KEY')
    if not key:raise SystemExit('FISHBOT_EVAL_API_KEY is required')
    root=Path(args.output);root.mkdir(parents=True,exist_ok=True)
    ledger=root/'requests.json'
    state=json.loads(ledger.read_text(encoding='utf-8')) if ledger.exists() else {'attempted':0,'results':[]}
    def save():
        temporary=root/'requests.tmp'
        temporary.write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding='utf-8')
        temporary.replace(ledger)
    async with httpx.AsyncClient(timeout=httpx.Timeout(120,connect=15),headers={'Authorization':'Bearer '+key},follow_redirects=False) as client:
        if args.discover:
            response=await client.get(args.base_url.rstrip('/')+'/models')
            if response.status_code!=200:raise SystemExit('Model discovery HTTP '+str(response.status_code))
            ids=[m['id'] for m in response.json().get('data',[]) if isinstance(m,dict) and isinstance(m.get('id'),str)]
            matches=[m for m in ids if m==args.model or '5.6' in m.lower() and 'luna' in m.lower()]
            print(json.dumps({'modelCandidates':matches,'exactRequestedAvailable':args.model in ids}),flush=True)
            return
        cases=json.loads(Path(args.cases).read_text(encoding='utf-8'))
        if not isinstance(cases,list):raise SystemExit('cases must be a list')
        semaphore=asyncio.Semaphore(2)
        async def execute(case):
            identifier=case['id']
            if any(r['id']==identifier for r in state['results']):return
            async with semaphore:
                if state['attempted']>=64:raise RuntimeError('64 attempted completion ceiling reached')
                body={**case['body'],'model':args.model,'stream':False}
                raw=json.dumps(body,ensure_ascii=False).encode()
                state['attempted']+=1
                record={'id':identifier,'sequence':state['attempted'],'requestSha256':hashlib.sha256(raw).hexdigest(),'requestBytes':len(raw),'status':'attempted'}
                state['results'].append(record);save()
                started=time.monotonic()
                try:
                    response=await client.post(args.base_url.rstrip('/')+'/chat/completions',content=raw,headers={'Content-Type':'application/json'})
                    record['httpStatus']=response.status_code
                    if response.status_code!=200:
                        # Provider error bodies may contain echoed authorization.
                        record['status']='http_error'
                    else:
                        value=response.json()
                        record.update(status='received',returnedModel=value.get('model'),usage=value.get('usage'),choices=value.get('choices'))
                except (httpx.HTTPError,ValueError) as error:
                    record.update(status='transport_or_decode_error',errorType=type(error).__name__)
                record['elapsedSeconds']=round(time.monotonic()-started,3);save()
                print(json.dumps({k:record[k] for k in ('id','sequence','status','elapsedSeconds')}),flush=True)
        await asyncio.gather(*(execute(case) for case in cases))
    print(json.dumps({'attemptedTotal':state['attempted'],'receivedTotal':sum(x['status']=='received' for x in state['results'])}),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url',default='https://nai.lunarbyte.pw/v1')
    parser.add_argument('--model',default='5.6luna')
    parser.add_argument('--cases')
    parser.add_argument('--output',required=True)
    parser.add_argument('--discover',action='store_true')
    options=parser.parse_args()
    if not options.discover and not options.cases:parser.error('--cases is required')
    asyncio.run(run(options))
