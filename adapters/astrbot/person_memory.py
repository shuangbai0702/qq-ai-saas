"""Request-local person views from committed Journal visibility, never a ledger."""
import copy,json,re
from context_protocol import source,model_json
from context_protocol import FOUNDATION_CURRENT
from memory_model_view import memory_json,FORMAT,expand
from decision_history import apply_changes
from relationships import relation_view
PERSON_CURRENT='[FishBot 人物记忆资料 v1]'


def profile(db,scope,person):
    from memory_scope import fact_rows,fact_item
    return {'facts':[fact_item(row,person,'checkpoint_foundation') for row in fact_rows(db,scope,person)],'relation':relation_view(db,person)}


def canonical(value):
    result=copy.deepcopy(value)
    result['facts'].sort(key=lambda r:(r.get('name',''),r.get('source_scope','')))
    for item in result['facts']:item.pop('recall_reason',None)
    if result.get('relation'):
        for key in ('observed_at','last_seen','last_message_at'):result['relation'].pop(key,None)
    return result


def visible(wire):
    people={}
    for message in wire:
        text=message.get('content')
        if message.get('role')!='user' or not isinstance(text,str):continue
        if text.startswith(FOUNDATION_CURRENT+'\n'):
            data=expand(json.loads(text.split('\n',1)[1]))
            for fact in data.get('facts',[]):
                person=str(fact.get('subject',''));people.setdefault(person,{'facts':[],'relation':None})['facts'].append(fact)
            for relation in data.get('relations',[]):
                person=str(relation.get('subject',''));people.setdefault(person,{'facts':[],'relation':None})['relation']=relation
            people={p:canonical(v) for p,v in people.items()}
        elif text.startswith(PERSON_CURRENT+'\n'):
            data=expand(json.loads(text.split('\n',1)[1]));person=data['subject']
            if 'changes' in data:people[person]=apply_changes(people.get(person,{'facts':[],'relation':None}),data['changes'])
            else:people[person]=canonical({'facts':data.get('facts',[]),'relation':data.get('relation')})
    return people


def field_changes(before,after,path=()):
    """Append changed fields only; never trade unchanged memory for packaging."""
    if type(before)!=type(after):return [['set',list(path),after]]
    if isinstance(before,dict):
        result=[['remove',list(path)+[key]] for key in before if key not in after]
        for key,value in after.items():
            result.extend(field_changes(before[key],value,(*path,key)) if key in before else [['set',list(path)+[key],value]])
        return result
    if isinstance(before,list):
        result=[]
        for index in range(min(len(before),len(after))):result.extend(field_changes(before[index],after[index],(*path,index)))
        result.extend(['remove',list(path)+[index]] for index in reversed(range(len(after),len(before))))
        result.extend(['set',list(path)+[index],after[index]] for index in range(len(before),len(after)))
        return result
    return [] if before==after else [['set',list(path),after]]


def additions(db,wire,incoming,enabled=True):
    if db is None or not enabled:return []
    people=visible(wire);packets=[]
    for message in incoming:
        origin=source(message)
        if not origin or origin.get('kind')=='platform_service_not_user':continue
        person=str(origin.get('sender_id',''));scope=origin.get('scope','')
        if not re.fullmatch(r'\d{5,20}',person) or not re.fullmatch(r'(private|group):\d{4,20}',scope):continue
        current=canonical(profile(db,scope,person));prior=people.get(person)
        if prior==current:continue
        if prior is None:
            if not current['facts'] and not current['relation']:continue
            packet={'subject':person,**current}
        else:
            delta=field_changes(prior,current)
            if not delta:continue
            packet={'subject':person,'changes':delta}
        packets.append({'role':'user','content':PERSON_CURRENT+'\n'+memory_json(packet)})
        people[person]=current
    return packets
