"""Read, validate and recover paid compression responses; never write cognition.

The original retry row is the only recovery authority. Product acceptance and
checkpoint transactions belong to their respective owners, outside this module.
"""
import asyncio,copy,hashlib,json,math,re,sqlite3
from context_protocol import model_json
from model_cache import SUMMARY_REQUEST_TIMEOUT_SECONDS

class SummaryPersistenceError(RuntimeError):pass


class SummaryResponseSlot:
    """Recover a response in the existing retry row; no new task authority."""
    def __init__(self,db,scope,attempt,epoch,digest,family=None,part=None):
        self.db=db;self.scope=scope;self.attempt=attempt;self.epoch=epoch;self.digest=digest
        self.family=family;self.part=part

    def payload(self,data):
        if not isinstance(data,dict):raise SummaryPersistenceError('summary_saved_response_invalid_shape')
        if self.part is None:return data
        if data.get('epoch')!=self.epoch or data.get('digest')!=self.family:return {}
        parts=data.get('parts',{})
        if not isinstance(parts,dict):raise SummaryPersistenceError('summary_saved_parts_invalid_shape')
        value=parts.get(self.part,{})
        if not isinstance(value,dict):raise SummaryPersistenceError('summary_saved_response_invalid_shape')
        return value

    def write(self,data):
        if self.part is not None:
            row=self.db.execute('SELECT result FROM memory_reflection_retry WHERE scope=?',(self.scope,)).fetchone()
            previous=json.loads(row[0]) if row and row[0] else {}
            parts=previous.get('parts',{}) if previous.get('epoch')==self.epoch and previous.get('digest')==self.family else {}
            data={'epoch':self.epoch,'digest':self.family,'parts':{**parts,self.part:data}}
        return self.db.execute('UPDATE memory_reflection_retry SET result=? WHERE scope=? AND attempts=?',(model_json(data),self.scope,self.attempt)).rowcount

    def recover(self):
        from types import SimpleNamespace
        row=self.db.execute('SELECT result FROM memory_reflection_retry WHERE scope=?',(self.scope,)).fetchone()
        if not row or not row[0]:return None
        try:data=json.loads(row[0])
        except (ValueError,TypeError) as error:raise SummaryPersistenceError('summary_saved_response_invalid_json') from error
        data=self.payload(data)
        if data.get('epoch')!=self.epoch or data.get('digest')!=self.digest:return None
        if data.get('phase')!='response_saved':return None
        content=data.get('text');raw=data.get('raw')
        if not isinstance(content,str) or hashlib.sha256(content.encode()).hexdigest()!=data.get('sha'):
            raise SummaryPersistenceError('summary_saved_response_integrity_failed')
        return SimpleNamespace(completion_text=content,raw_completion=raw,is_chunk=False)

    def validation_failures(self):
        try:
            row=self.db.execute('SELECT result FROM memory_reflection_retry WHERE scope=?',(self.scope,)).fetchone()
            if not row or not row[0]:return []
            data=self.payload(json.loads(row[0]))
            failures=data.get('validation_errors',[]) if data.get('epoch')==self.epoch and data.get('digest')==self.digest else []
            if not isinstance(failures,list):raise SummaryPersistenceError('summary_saved_validation_invalid_shape')
            return failures[-4:]
        except SummaryPersistenceError:raise
        except Exception as error:raise SummaryPersistenceError('summary_validation_read_failed') from error

    def validation_evidence(self):
        """Bounded transaction audit of all pages, not only the final response."""
        try:
            row=self.db.execute('SELECT result FROM memory_reflection_retry WHERE scope=?',(self.scope,)).fetchone()
            if not row or not row[0]:return {'total':0,'omitted':0,'errors':[]}
            data=json.loads(row[0]);self.payload(data)
            if self.part is None:parts={'final':data}
            elif data.get('epoch')==self.epoch and data.get('digest')==self.family:parts=data.get('parts',{})
            else:parts={}
            errors=[];total=0
            for part,value in sorted(parts.items()):
                if not isinstance(value,dict):raise SummaryPersistenceError('summary_saved_parts_invalid_shape')
                failures=value.get('validation_errors',[])
                if not isinstance(failures,list):raise SummaryPersistenceError('summary_saved_validation_invalid_shape')
                total+=len(failures)
                for failure in failures:
                    if len(errors)<16:errors.append({**failure,'part':part})
            return {'total':total,'omitted':max(0,total-len(errors)),'errors':errors}
        except SummaryPersistenceError:raise
        except Exception as error:raise SummaryPersistenceError('summary_validation_read_failed') from error

    def record_validation(self,error,attempt):
        try:
            with self.db:
                row=self.db.execute('SELECT result FROM memory_reflection_retry WHERE scope=?',(self.scope,)).fetchone()
                data=self.payload(json.loads(row[0])) if row and row[0] else {}
                if data.get('epoch')!=self.epoch or data.get('digest')!=self.digest:raise SummaryPersistenceError('summary_validation_owner_changed')
                failures=data.get('validation_errors',[])
                data['validation_errors']=(failures+[{'attempt':attempt,'stage':error.stage,'path':error.path,'code':error.code}])[-4:]
                if self.write(data)!=1:raise SummaryPersistenceError('summary_validation_owner_changed')
        except SummaryPersistenceError:raise
        except Exception as failure:raise SummaryPersistenceError('summary_validation_persistence_failed') from failure

    def dispatch(self):
        with self.db:
            payload={'epoch':self.epoch,'digest':self.digest,'attempt':self.attempt,'phase':'dispatched','validation_errors':self.validation_failures()}
            if self.write(payload)!=1:
                raise SummaryPersistenceError('summary_request_owner_changed')

    def save(self,response):
        if getattr(response,'is_chunk',False):raise SummaryPersistenceError('summary_partial_response_not_saved')
        text=getattr(response,'completion_text',None)
        # A malformed provider response is eligible for bounded output repair;
        # it is not a database failure. Never save a non-text response as paid
        # recoverable cognition, nor bypass the normal schema checks.
        if not isinstance(text,str):raise SummaryValidationError('response_text_missing','$.completion_text')
        from model_cache import response_shape,usage_evidence
        raw=getattr(response,'raw_completion',None)
        # Preserve final/usage evidence, not duplicated reasoning or credentials.
        usage=raw.get('usage') if isinstance(raw,dict) else getattr(raw,'usage',None)
        if callable(getattr(usage,'model_dump',None)):usage=usage.model_dump()
        data={'epoch':self.epoch,'digest':self.digest,'attempt':self.attempt,'phase':'response_saved','text':text,'sha':hashlib.sha256(text.encode()).hexdigest(),
              'validation_errors':self.validation_failures(),
              'raw':{'choices':[{'finish_reason':response_shape(raw).get('finishReason')}],'usage':usage,'id':usage_evidence(response).get('responseId')}}
        try:
            # Retry only a transient SQLite write lock, reusing this exact paid
            # response and rechecking the owner. Never ask the model to repair DB.
            for persistence_attempt in range(2):
                try:
                    with self.db:
                        current=self.db.execute('SELECT epoch FROM memory_reflection_control WHERE id=1').fetchone()
                        if not current or current[0]!=self.epoch:return
                        saved=self.write(data)
                        if saved!=1:raise SummaryPersistenceError('summary_response_owner_changed')
                    break
                except sqlite3.OperationalError as error:
                    code=getattr(error,'sqlite_errorcode',0)&255
                    if persistence_attempt or code not in (sqlite3.SQLITE_BUSY,sqlite3.SQLITE_LOCKED):raise
        except SummaryPersistenceError:raise
        except Exception as error:raise SummaryPersistenceError('summary_response_persistence_failed') from error



def summary_request_digest(request,provider,epoch,job,anchor):
    """Only the formatter's current clock is excluded; all evidence stays fenced."""
    value=dict(request)
    prompt=json.loads(value['prompt']);prompt.pop('current_time',None);value['prompt']=prompt
    config=getattr(provider,'provider_config',{}) or {}
    identity={k:config.get(k) for k in ('id','model','api_base','type','temperature','top_p','max_tokens','reasoning_effort','model_config','custom_parameters')}
    return hashlib.sha256(model_json({'request':value,'provider':identity,'epoch':epoch,'job':job,'anchor':anchor}).encode()).hexdigest()



class SummaryValidationError(ValueError):
    """Safe, bounded error paths; never echo untrusted model text in policy."""
    def __init__(self,code,path='$',stage='structure'):
        self.code=code;self.path=path;self.stage=stage
        super().__init__(code)


def decode_summary_object(text):
    if not isinstance(text,str):raise SummaryValidationError('response_text_missing')
    raw=text.strip().lstrip('\ufeff').strip()
    fenced=re.fullmatch(r'```(?:json)?\s*\n(.*?)\n```',raw,re.S|re.I)
    if fenced:raw=fenced[1]
    def unique_object(pairs):
        result={}
        for key,value in pairs:
            if key in result:raise SummaryValidationError('duplicate_json_key')
            result[key]=value
        return result
    def invalid_constant(value):raise SummaryValidationError('nonfinite_json_number')
    def finite_float(text):
        value=float(text)
        if not math.isfinite(value):raise SummaryValidationError('nonfinite_json_number')
        return value
    try:data=json.loads(raw,object_pairs_hook=unique_object,parse_constant=invalid_constant,parse_float=finite_float)
    except SummaryValidationError:raise
    except (ValueError,TypeError,RecursionError) as error:raise SummaryValidationError('invalid_summary_json',stage='json') from error
    if not isinstance(data,dict):raise SummaryValidationError('expected_json_object')
    return data


def validate_summary_output(response):
    from model_cache import response_shape
    reason=response_shape(getattr(response,'raw_completion',None)).get('finishReason')
    if reason in ('length','max_tokens'):
        raise SummaryValidationError('summary_output_truncated_material_retained','$.finish_reason','transport')
    if reason!='stop':raise SummaryValidationError('invalid_summary_finish_reason','$.finish_reason','transport')


async def summary_model_output(provider,request,live,metrics,limit,on_decode=None,on_attempt=None,on_response=None,recovered=None,on_validation_error=None):
    """Retry only a complete model read and its mandatory output validation.

    No product writes or transaction callbacks belong inside this boundary.
    The hosted provider disables upstream retries; each iteration is one request.
    """
    from contextlib import aclosing
    from output_clean import clean
    request=copy.deepcopy(request)
    nonstream=False
    validation_errors=[]
    async def read():
        if nonstream:
            # Same bound Provider/transport, identity, budget and diagnostics.
            # Only read-only summarization may retry; no product is committed
            # until the complete response passes the normal validation below.
            response=await provider.text_chat(**copy.deepcopy(request))
            if on_response:on_response(response)
            return response
        final=None;validation_error=None
        try:
            async with aclosing(provider.text_chat_stream(**copy.deepcopy(request))) as responses:
                async for response in responses:
                    if not getattr(response,'is_chunk',False):
                        final=response
                        if on_response:
                            try:on_response(response)
                            except SummaryValidationError as error:validation_error=error
                    if not live():return None
        except SummaryPersistenceError:raise
        except Exception:
            if final is None:raise
            # A complete final response remains valid evidence even if closing
            # its transport fails. Normal finish/JSON checks still follow.
            metrics['summaryStreamErrorsAfterFinal']=metrics.get('summaryStreamErrorsAfterFinal',0)+1
        if final is None:raise ValueError('incomplete_summary_stream')
        if validation_error:raise validation_error
        return final
    for attempt in range(2):
        if not live():return None
        restored=recovered if attempt==0 else None
        if restored is None:
            if on_attempt:on_attempt()
            metrics['summaryRequests']=metrics.get('summaryRequests',0)+1
        try:
            response=restored if restored is not None else await asyncio.wait_for(read(),SUMMARY_REQUEST_TIMEOUT_SECONDS+5)
            if restored is not None:metrics['summaryResponsesRecovered']=metrics.get('summaryResponsesRecovered',0)+1
            if not live():return None
            validate_summary_output(response)
            try:data=decode_summary_object(getattr(response,'completion_text',None))
            except SummaryValidationError:
                if on_decode:on_decode({}, {'decode_status':'invalid_json'})
                raise
            diagnostics={'decode_status':'json_object'}
            # Product validators stay outside corrective generation authority:
            # a scope/identity/CAS rejection must not be delegated to the model.
            if on_decode:on_decode(data,diagnostics)
            if limit is not None:
                if 'summary' not in data:raise SummaryValidationError('missing_required_field','$.summary')
                if not isinstance(data['summary'],str):raise SummaryValidationError('expected_string','$.summary')
                text=clean(data['summary']).strip()
                if not text:raise SummaryValidationError('empty_summary','$.summary')
                if len(text)>limit:raise SummaryValidationError('summary_output_capacity_exceeded_material_retained','$.summary')
            else:text=''
            return response,data,text
        except (asyncio.CancelledError,SummaryPersistenceError):raise
        except Exception as error:
            if not live():return None
            if isinstance(error,SummaryValidationError):
                failure={'attempt':attempt+1,'stage':error.stage,'path':error.path,'code':error.code}
                validation_errors.append(failure)
                metrics['summaryValidationErrors']=list(validation_errors)
                if on_validation_error:on_validation_error(error,attempt+1)
            if attempt:raise
            if isinstance(error,SummaryValidationError):
                feedback=f' 上次响应校验失败：{error.path}: {error.code}。重新输出原合同要求的完整JSON对象；仅纠正格式或结构，不改变原始材料、身份、权限、版本和产品合同，不猜测补全。'
                if error.code=='summary_output_capacity_exceeded_material_retained':
                    feedback+=f' summary不超过{limit}字符，目标{max(1,limit*3//4)}字符；按语义合并重复描述，保留关键人物、条件、纠正和未完事项，不截断句子，不增加新结论。'
                    metrics['summaryCapacityRetries']=metrics.get('summaryCapacityRetries',0)+1
                request={**request,'system_prompt':str(request.get('system_prompt') or '')+feedback}
            from httpx import TransportError
            from openai import APIConnectionError,APIError,InternalServerError
            transport_error=(isinstance(error,(TransportError,APIConnectionError,InternalServerError,OSError,asyncio.TimeoutError))
                # OpenAI's SSE iterator raises the base APIError for in-band
                # error events. HTTP client errors use APIStatusError subclasses.
                or type(error) is APIError
                or isinstance(error,ValueError) and str(error)=='incomplete_summary_stream'
                # The hosted provider detects EOF first and uses this exact
                # signal. Recover on the same bound nonstream transport too.
                or isinstance(error,RuntimeError) and str(error)=='incomplete_model_stream'
            )
            if not isinstance(error,SummaryValidationError) and not transport_error:raise
            if transport_error and callable(getattr(provider,'text_chat',None)):
                nonstream=True
                metrics['summaryNonstreamFallbacks']=metrics.get('summaryNonstreamFallbacks',0)+1
            metrics['summaryImmediateRetries']=metrics.get('summaryImmediateRetries',0)+1
