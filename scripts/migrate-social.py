"""Offline preflight for the bounded social schema migration. Never deletes data.

Run after graceful platform shutdown. Each per-bot SQLite is backed up first.
The normal runtime also performs this migration lazily when opening a Bot store.
"""
import json,sqlite3,sys,time
from pathlib import Path
import psutil
root=Path(__file__).resolve().parents[1];data=(root/'.local').resolve()
lock=data/'agent.lock'
if lock.exists() and psutil.pid_exists(int(lock.read_text())):
    raise SystemExit('Stop the platform gracefully before offline social migration')
sys.path.insert(0,str(root/'adapters/astrbot'))
from social import SocialStore
backup=data/'backups'/('social-schema-'+time.strftime('%Y%m%d-%H%M%S'))
backup.mkdir(parents=True,exist_ok=False);report=[]
for file in sorted((data/'bots').glob('*/behavior.sqlite')):
    if not file.resolve().is_relative_to(data/'bots'):raise RuntimeError('Unexpected database outside bot root')
    db=sqlite3.connect(file)
    try:
        target=sqlite3.connect(backup/(file.parent.name+'.sqlite'))
        try:db.backup(target)
        finally:target.close()
        before=db.execute('SELECT * FROM facts ORDER BY scope,subject,name,version').fetchall()
        started=time.perf_counter();store=SocialStore(db,file.parent);store.close()
        after=db.execute('SELECT * FROM facts ORDER BY scope,subject,name,version').fetchall()
        assert before==after,'Fact rows changed during social-only migration'
        assert 'AUTOINCREMENT' in db.execute("SELECT sql FROM sqlite_master WHERE name='social_samples'").fetchone()[0]
        report.append({'botId':file.parent.name,'factRowsPreserved':len(after),'milliseconds':round((time.perf_counter()-started)*1000,2)})
    finally:db.close()
result={'backup':str(backup),'databases':report,'factRowsUnchanged':True}
(backup/'result.json').write_text(json.dumps(result,indent=2),encoding='utf8')
out=root/'test-results/social-audit/migration.json';out.write_text(json.dumps(result,indent=2),encoding='utf8')
print('Migrated',len(report),'bot databases; fact rows unchanged; backups preserved')
