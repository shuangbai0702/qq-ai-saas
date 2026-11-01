"""Same-bot, trusted-QQ memory sharing; original records keep their source scope.

Callers own one bot's SQLite connection. This module never opens another bot's
database or infers a person from chat text, nicknames, or model output.
"""
import json,re


def shared_person(scope,subject):
    return bool(re.fullmatch(r'\d{5,20}',str(subject)) and
                (scope=='private:'+str(subject) or re.fullmatch(r'group:\d{4,20}',scope)))


def person_filter(scope,subject,alias=''):
    prefix=alias+'.' if alias else ''
    if shared_person(scope,subject):
        return prefix+"subject=? AND ("+prefix+"scope LIKE 'group:%' OR "+prefix+"scope=?)",(subject,'private:'+subject)
    return prefix+'scope=? AND '+prefix+'subject=?',(scope,subject)


def fact_rows(db,scope,subject,limit=200):
    where,args=person_filter(scope,subject,'f');blocked,block_args=person_filter(scope,subject,'b')
    # Explicit records win over automatic captures. Among equal origins the
    # latest source wins; legacy duplicate records are read without data loss.
    return db.execute('''SELECT name,value,event,version,updated,kind,importance,origin,last_recalled,recalls,source_scope FROM (
      SELECT f.name,f.value,f.event,f.version,f.updated,coalesce(m.kind,'stable') AS kind,
        coalesce(m.importance,.8) AS importance,coalesce(m.origin,'explicit') AS origin,
        coalesce(m.last_recalled,0) AS last_recalled,coalesce(m.recalls,0) AS recalls,f.scope AS source_scope,
        row_number() OVER (PARTITION BY f.name ORDER BY (coalesce(m.origin,'explicit')='explicit') DESC,f.updated DESC,f.version DESC,f.scope) AS choice
      FROM facts f LEFT JOIN fact_metadata m ON f.scope=m.scope AND f.subject=m.subject AND f.name=m.name
      WHERE '''+where+''' AND f.active=1 AND NOT EXISTS (
        SELECT 1 FROM memory_blocks b WHERE '''+blocked+''' AND b.name=f.name)
    ) WHERE choice=1 ORDER BY updated DESC LIMIT ?''',args+block_args+(min(200,max(1,limit)),)).fetchall()


def fact_item(row,subject,reason):
    return {'name':row[0],'value':row[1],'source_event':row[2],'version':row[3],
            'remembered_at':row[4],'kind':row[5],'origin':row[7],
            'source_scope':row[10],'subject':subject,'recall_reason':reason}


def summary_rows(db,scope,subject='',limit=1000):
    # Other conversations enter recall only with protocol-backed participation
    # evidence for the current QQ, never because its ID appears in model text.
    if shared_person(scope,subject):
        return db.execute('''SELECT s.id,s.text,s.sources,s.updated,s.scope FROM memory_summaries s
          WHERE s.scope=? OR ((s.scope LIKE 'group:%' OR s.scope=?) AND EXISTS (SELECT 1 FROM memory_summary_people p WHERE p.summary_id=s.id AND p.subject=?))
          ORDER BY s.updated DESC,s.id DESC LIMIT ?''',(scope,'private:'+subject,subject,min(1000,limit))).fetchall()
    return db.execute('SELECT id,text,sources,updated,scope FROM memory_summaries WHERE scope=? ORDER BY updated DESC,id DESC LIMIT ?',(scope,min(1000,limit))).fetchall()


def summary_item(row,reason=None):
    result={'id':row[0],'summary':row[1],'source_events':json.loads(row[2]),'at':row[3],
            'source_scope':row[4],'kind':'conversation_summary_not_verified_facts'}
    if reason:result['recall_reason']=reason
    return result


def pending_messages(db,scope,subject,now,limit=16,budget=3000):
    if not shared_person(scope,subject):return []
    where,args=person_filter(scope,subject)
    rows=db.execute('SELECT scope,subject,event,question,answer,at FROM memory_pending WHERE '+where+' AND scope!=? AND at>? AND at<=? ORDER BY at DESC,rowid DESC LIMIT ?',args+(scope,now-7*86400,now,min(16,max(1,limit)))).fetchall()
    result=[];used=0
    for source,person,event,question,answer,at in rows:
        question=question[:400];answer=answer[:400]
        if used+len(question)+len(answer)>budget:continue
        result.append({'kind':'recent_uncompressed_message','source_scope':source,'sender_id':person,'source_event':event,'at':at,'age_seconds':max(0,int(now-at)),'user':question,'bot':answer})
        used+=len(question)+len(answer)
    return list(reversed(result))
