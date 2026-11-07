"""D9 legacy memory preview: read-only scan for mis-attributed material.

Reports what a production migration would have to decide about, without
writing anything: cross-scope facts, legacy mixed-origin summaries, unknown
core notes and value-block rows. Zero writes by construction (read-only
connection); the fixture test asserts that.
"""
import json,os,re,sqlite3,sys
from urllib.parse import quote


def open_readonly(path):
    # Immutable+ro URI: SQLite refuses writes at the driver level.
    uri='file:'+quote(os.path.abspath(path).replace('\\','/'))+'?mode=ro&immutable=1'
    return sqlite3.connect(uri,uri=True)


def tables(db):
    return {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def preview(db):
    """Collect a bounded, human-reviewable list of legacy anomalies."""
    found=tables(db);report={}
    if 'facts' in found:
        # A private scope row whose subject is not the private owner is the
        # classic early cross-scope mis-attribution.
        rows=db.execute("SELECT scope,subject,name,value,event FROM facts WHERE active=1 AND scope LIKE 'private:%' AND subject<>substr(scope,9) LIMIT 200").fetchall()
        if rows:report['facts_cross_scope']=rows
    if 'memory_summaries' in found and 'memory_summary_origins' in found:
        # Mixed-origin summaries cannot be split without their original
        # material; they keep audience-gated visibility and are listed here.
        mixed=[row for row in db.execute('''SELECT s.id,s.scope,group_concat(o.scope) FROM memory_summaries s
          JOIN memory_summary_origins o ON o.summary_id=s.id GROUP BY s.id HAVING count(DISTINCT o.scope)>1 ORDER BY s.id LIMIT 200''').fetchall()]
        if mixed:report['summaries_mixed_origins']=mixed
    if 'memory_core_notes' in found:
        rows=db.execute('SELECT text,scopes,people FROM memory_core_notes').fetchall()
        legacy=[]
        for text,scopes,people in rows:
            try:value=json.loads(scopes)
            except (ValueError,TypeError):value=[]
            if not isinstance(value,list) or not value or '*' in value:legacy.append([text[:120],scopes])
        if legacy:report['core_notes_legacy_unverified']=legacy
    if 'memory_blocks' in found:
        rows=db.execute("SELECT scope,subject,name,block_all,revision FROM memory_blocks WHERE scope LIKE 'private:%' AND subject<>substr(scope,9) LIMIT 200").fetchall()
        if rows:report['blocks_cross_scope']=rows
    if 'memory_pending' in found:
        rows=db.execute("SELECT scope,subject,count(*) FROM memory_pending WHERE scope LIKE 'private:%' AND subject<>substr(scope,9) GROUP BY scope,subject LIMIT 200").fetchall()
        if rows:report['pending_cross_scope']=rows
    return report


def main(argv):
    paths=argv[1:]
    if not paths:
        print('usage: python d9_legacy_migration_dryrun.py <bot.sqlite> [more.sqlite ...]')
        return 2
    for path in paths:
        db=open_readonly(path)
        try:
            report=preview(db)
            print(json.dumps({'file':os.path.basename(path),'anomalies':report},ensure_ascii=False,indent=1))
        finally:
            db.close()
    return 0


if __name__=='__main__':
    sys.exit(main(sys.argv))
