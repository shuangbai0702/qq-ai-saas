"""One-shot maintenance transfer of minimal confirmed ownership receipts.

No requests, tool results or execution history are copied. The normal release
owner must hold every original worker/executor fence before invoking this file.
"""
import argparse,hashlib,json,sqlite3
from contextlib import closing
from pathlib import Path


def transfer(root,records):
    root=Path(root).resolve();count=0
    for record in records:
        if set(record)!={'bot_id','tenant_id','id','operation','args_digest','receipt','at'}:raise ValueError('receipt_fields_invalid')
        if record['operation'] not in ('comment','dynamic_post'):raise ValueError('receipt_operation_invalid')
        receipt=record['receipt']
        if set(receipt)!={'account','aid','status','platformObjectId'} or receipt['status']!='confirmed' or not receipt['platformObjectId']:raise ValueError('receipt_not_confirmed')
        # Reject separators and reparse points before touching a named owner DB.
        bot=record['bot_id']
        if not isinstance(bot,str) or not bot or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-' for c in bot):raise ValueError('receipt_bot_invalid')
        folder=root/'bots'/bot;file=folder/'behavior.sqlite'
        for p in (root/'bots',folder,file):
            if p.is_symlink() or getattr(p.lstat(),'st_file_attributes',0)&0x400:raise ValueError('receipt_reparse_owner')
        if file.resolve().parent!=folder.resolve() or not file.resolve().is_relative_to(root):raise ValueError('receipt_path_owner')
        with closing(sqlite3.connect(file,timeout=10)) as db,db:
            db.execute('PRAGMA secure_delete=ON')
            before=db.execute('SELECT scope,version,state,sha,updated FROM fishbot_readonly_journals ORDER BY scope').fetchall()
            existing=db.execute('SELECT method,target,state,receipt FROM pipeline_action_effects WHERE action=? AND sequence=0',(record['id'],)).fetchone()
            method='bilibili_'+record['operation'];target=json.dumps([receipt['account'],record['args_digest']],separators=(',',':'))
            if existing:
                if existing[:3]!=(method,target,'confirmed') or json.loads(existing[3])!=receipt:raise ValueError('receipt_identity_conflict')
            else:
                db.execute("INSERT INTO pipeline_action_effects(action,sequence,method,target,state,receipt,at) VALUES(?,0,?,?,'confirmed',?,?)",(record['id'],method,target,json.dumps(receipt,separators=(',',':')),record['at']))
            assert db.execute('SELECT scope,version,state,sha,updated FROM fishbot_readonly_journals ORDER BY scope').fetchall()==before
            assert json.loads(db.execute('SELECT receipt FROM pipeline_action_effects WHERE action=? AND sequence=0',(record['id'],)).fetchone()[0])==receipt
        count+=1
    return count


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--data-root',required=True);parser.add_argument('--receipts',required=True);parser.add_argument('--maintenance-permit',required=True)
    args=parser.parse_args();permit=json.loads(Path(args.maintenance_permit).read_text(encoding='utf-8-sig'))
    import time
    if permit.get('fenced') is not True or not 0<=time.time()-permit['at']<120:raise ValueError('maintenance_permit_expired')
    raw=Path(args.receipts).read_bytes();records=json.loads(raw)
    print(json.dumps({'ok':True,'verified':transfer(args.data_root,records),'manifestSha256':hashlib.sha256(raw).hexdigest()}))
