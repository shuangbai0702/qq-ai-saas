"""JSON-line transport over the real original SQLite Pipeline, test only."""
import json,sqlite3,sys,time
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'adapters/astrbot'))
from bot_pipeline import BotPipeline
import pipeline_tools
sys.stdout.reconfigure(encoding='utf-8');sys.stdin.reconfigure(encoding='utf-8')
db=sqlite3.connect(sys.argv[1]);pipeline=BotPipeline(db);settings=None
for line in sys.stdin:
    request=json.loads(line)
    try:
        if request['operation']=='initialize':
            settings=request['settings'];identity={**settings,'id':request['workId'],'cycleId':request['workId'],'epoch':request['epoch'],'kind':'research','expiresAt':(time.time()+300)*1000}
            pipeline.bind_internal(SimpleNamespace(get_self_id=lambda:'12345',set_extra=lambda *args:None),settings,identity,'self','自由选择')
            result=True
        else:result=pipeline_tools.handle(pipeline,settings,request['operation'],request.get('data',{}))
        print(json.dumps({'id':request['id'],'result':result},ensure_ascii=False),flush=True)
    except Exception as error:print(json.dumps({'id':request['id'],'error':type(error).__name__+':'+str(error)}),flush=True)
db.close()
