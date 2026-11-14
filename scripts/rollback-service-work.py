"""Release rollback only: stop the exact Worker before restoring old code.

Transfers the current Work owner back; never restores a database backup or
changes a submitted Journal. Not imported by the application.
"""
import sqlite3,sys

def rollback(db):
    db.execute('SAVEPOINT rollback_service_work')
    try:
        db.execute('CREATE TABLE pipeline_actions(id TEXT PRIMARY KEY,fingerprint TEXT,epoch INTEGER,scope TEXT,expires REAL,state TEXT,result TEXT,at REAL)')
        db.execute("""INSERT INTO pipeline_actions SELECT event,fingerprint,authority_epoch,scope,expires_at,
          CASE WHEN result IS NOT NULL AND json_valid(result) THEN coalesce(json_extract(result,'$.status'),status)
               WHEN status IN ('received','sending') THEN 'interrupted' ELSE status END,result,at
          FROM pipeline_events WHERE source='autonomous'""")
        db.execute("DELETE FROM pipeline_events WHERE source='autonomous'")
        db.execute('DROP INDEX IF EXISTS pipeline_service_event')
    except BaseException:
        db.execute('ROLLBACK TO rollback_service_work')
        raise
    finally:db.execute('RELEASE rollback_service_work')

if __name__=='__main__':
    if len(sys.argv)!=2:raise SystemExit('Usage: rollback-service-work.py DATABASE (Worker must be stopped)')
    with sqlite3.connect(sys.argv[1]) as connection:rollback(connection)
