"""Content-free receipts for the existing reflection transaction, never memory.

No values, expected snapshots, model text, event IDs or source lists are stored.
Targets use trusted author IDs and hashed field names; forgetting removes detail.
"""
import hashlib,json,time,uuid


def initialize(db):
    db.execute('''CREATE TABLE IF NOT EXISTS memory_operation_audits(
      id TEXT PRIMARY KEY,scope TEXT NOT NULL,epoch INTEGER NOT NULL,job TEXT NOT NULL,
      checkpoint_id INTEGER,summary_id INTEGER,state TEXT NOT NULL,reason TEXT NOT NULL,
      counts TEXT NOT NULL,created REAL NOT NULL,decode_status TEXT NOT NULL,
      through_round_id INTEGER)''')
    db.execute('''CREATE TABLE IF NOT EXISTS memory_operation_audit_details(
      audit_id TEXT NOT NULL,category TEXT NOT NULL,position INTEGER NOT NULL,
      subject TEXT NOT NULL,target_hash TEXT NOT NULL,operation TEXT NOT NULL,
      decision TEXT NOT NULL,reason TEXT NOT NULL,changed INTEGER NOT NULL,
      PRIMARY KEY(audit_id,category,position))''')


def forget(db,scope='',subject='',summary_id=None,relations_only=False):
    # A shared reflection may contain this person's material from another scope.
    # Remove complete attempt details conservatively, keeping only aggregate counts.
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='memory_operation_audits'").fetchone():return
    if summary_id is not None:
        where='summary_id=?';args=(summary_id,)
    elif subject:
        where="scope=? OR id IN (SELECT audit_id FROM memory_operation_audit_details WHERE subject=? OR subject='')";args=(scope,subject)
    else:where='scope=?';args=(scope,)
    category=" AND category='relations'" if relations_only else ''
    ids=[row[0] for row in db.execute('SELECT id FROM memory_operation_audits WHERE '+where,args)]
    for identifier in ids:
        db.execute('DELETE FROM memory_operation_audit_details WHERE audit_id=?'+category,(identifier,))
        if not relations_only:
            db.execute("UPDATE memory_operation_audits SET scope='',checkpoint_id=NULL,summary_id=NULL,through_round_id=NULL WHERE id=?",(identifier,))


class Audit:
    def __init__(self,scope,epoch,job,data,subjects=(),decode_status='json_object',through_round_id=None):
        self.id=str(uuid.uuid4());self.scope=scope;self.epoch=epoch;self.job=job
        self.decode_status=decode_status
        self.through_round_id=through_round_id
        self.counts={};self.details={};people=set(map(str,subjects))
        for category,key in (('facts','fact_operations'),('relations','relation_operations'),('core_note','core_note'),('products','memory_operations')):
            raw=data.get(key)
            items=([raw] if category=='core_note' else raw) if key in data else []
            valid=isinstance(items,list)
            rows=items if valid else [None]
            self.counts[category]={'proposed':len(rows),'accepted':0,'rejected':0,'not_evaluated':len(rows),'omitted':None if decode_status!='json_object' else key not in data,'malformed':not valid}
            for position,item in enumerate(rows):
                person=item.get('subject') if isinstance(item,dict) else None
                name=item.get('name') if isinstance(item,dict) else None
                if category=='products' and isinstance(item,dict) and isinstance(item.get('target'),dict):
                    name=json.dumps(item['target'],sort_keys=True,separators=(',',':'))
                    person=item['target'].get('subject')
                operation=item.get('op') if isinstance(item,dict) else None
                entry={'subject':person if isinstance(person,str) and person in people else '',
                       'target_hash':hashlib.sha256(name.encode()).hexdigest() if isinstance(name,str) else '',
                       'operation':operation if operation in ('add','revise','merge','weaken','forget','create','replace','delete') else 'update' if category!='facts' else 'invalid',
                       'decision':'not_evaluated','reason':'not_evaluated','changed':False}
                self.details[(category,position)]=entry
                if not valid:self.mark(category,position,'rejected','invalid_container')

    def mark(self,category,position,decision,reason,changed=False):
        entry=self.details.get((category,position))
        if entry is None:return
        count=self.counts[category];count[entry['decision']]-=1;count[decision]+=1
        entry.update(decision=decision,reason=reason,changed=bool(changed))

    def persist(self,db,state,reason,checkpoint_id=None,summary_id=None,details=True):
        initialize(db)
        db.execute('INSERT INTO memory_operation_audits VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                   (self.id,self.scope if details else '',self.epoch,self.job,checkpoint_id if details else None,summary_id if details else None,state,reason,json.dumps(self.counts,separators=(',',':')),time.time(),self.decode_status,self.through_round_id if details else None))
        if details:
            db.executemany('INSERT INTO memory_operation_audit_details VALUES(?,?,?,?,?,?,?,?,?)',
                [(self.id,category,position,row['subject'],row['target_hash'],row['operation'],row['decision'],row['reason'],int(row['changed'])) for (category,position),row in self.details.items()])
        # Operational history has a fixed bound, independent of chat volume.
        db.execute('DELETE FROM memory_operation_audit_details WHERE audit_id IN (SELECT id FROM memory_operation_audits ORDER BY created DESC LIMIT -1 OFFSET 256)')
        db.execute('DELETE FROM memory_operation_audits WHERE id IN (SELECT id FROM memory_operation_audits ORDER BY created DESC LIMIT -1 OFFSET 256)')
