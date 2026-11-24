"""Bounded model copies with trusted ownership records for batch originals."""
import asyncio,base64,io,json,os,re,tempfile,warnings
from pathlib import Path
from image_codec import POLICY,MAX_EDGE,target_edge,limit_data_url,_limited,_run_codec


def request_memo(event):
    if event is None:return {}
    memo=event.get_extra('_hosted_model_image_memo')
    if memo is None:
        memo={};event.set_extra('_hosted_model_image_memo',memo)
    return memo


def install_sdk_preparation():
    """Apply source tiers before the SDK loses original sizes or animation."""
    from astrbot.core.utils import image_input
    if getattr(image_input.prepare_model_image,'_fishbot_tiers',False):return
    original=image_input.prepare_model_image
    async def prepare(image_ref,*,max_size,output_dir):
        from astrbot.core.platform.astr_message_event import current_message_event
        event=current_message_event.get()
        if event is None or event.get_extra('_hosted_lease') is None:
            return await original(image_ref,max_size=max_size,output_dir=output_dir)
        from astrbot.core.utils.media_utils import MediaResolver,ImageInputTooLargeError,is_recoverable_image_error
        try:
            async with MediaResolver(image_ref,media_type='image').as_path() as source:
                if source.path.stat().st_size>POLICY['maxSourceBytes']:
                    source.detach();raise ImageInputTooLargeError(str(source.path))
                raw=await asyncio.to_thread(source.read_bytes)
                from PIL import Image
                with Image.open(io.BytesIO(raw)) as image:mime=Image.MIME.get(image.format,'')
                value='data:'+mime+';base64,'+base64.b64encode(raw).decode('ascii');details={}
                limited=await _limited(value,details)
                original_path=str(source.path)
                # Only resolver-owned cleanup paths grant deletion authority.
                # Borrowed local files, persistent stickers and path notices do
                # not. Stage identity evidence on this event; BehaviorStore
                # binds it to actual captured model material before submission.
                from semantic_media import owned_record
                from astrbot.core.utils.astrbot_path import get_astrbot_temp_path
                originals=list(event.get_extra('_hosted_media_originals') or [])
                originals.extend(owned_record(file,get_astrbot_temp_path(),[value,limited]) for file in source.cleanup_paths)
                event.set_extra('_hosted_media_originals',originals)
                source.detach()
            # The SDK owns returned working files through its existing event
            # tracker. No background write can outlive publication/cancellation.
            output_dir=Path(output_dir);output_dir.mkdir(parents=True,exist_ok=True)
            suffix='.jpg' if limited.startswith('data:image/jpeg;') else '.png'
            fd,name=tempfile.mkstemp(prefix='model_image_',suffix=suffix,dir=output_dir)
            try:
                with os.fdopen(fd,'wb') as output:output.write(base64.b64decode(limited.split(',',1)[1]))
            except BaseException:
                Path(name).unlink(missing_ok=True);raise
            request_memo(event)[limited]=(limited,False)
            return name,details.get('animated',False),True,original_path
        except ImageInputTooLargeError:raise
        except Exception as error:
            if not is_recoverable_image_error(error):raise
            return None
    prepare._fishbot_tiers=True
    image_input.prepare_model_image=prepare


def _image_shape(message):
    """Match a submitted physical message without discarding non-image fields."""
    if not isinstance(message,dict) or not isinstance(message.get('content'),list):return None
    value={**message,'content':[{**p,'image_url':{**{k:v for k,v in p['image_url'].items() if k!='id' or v is not None},'url':'[image]'}} if isinstance(p,dict) and p.get('type')=='image_url' and isinstance(p.get('image_url'),dict) else p for p in message['content']]}
    return json.dumps(value,ensure_ascii=False,separators=(',',':'))


async def constrain_messages(messages,memo=None,retained=None):
    """Prepare only never-submitted image inputs; Journal wire bypasses this."""
    if not isinstance(messages,list):return messages
    result=[];memo={} if memo is None else memo
    submitted={}
    for retained_message in retained or []:
        shape=_image_shape(retained_message)
        if shape is not None:submitted.setdefault(shape,[]).append(retained_message)
    for original in messages:
        if not isinstance(original,dict) or not isinstance(original.get('content'),list):result.append(original);continue
        message=dict(original);parts=[]
        prior=submitted.get(_image_shape(original),[])
        for index,part in enumerate(original['content']):
            if not isinstance(part,dict) or part.get('type')!='image_url':parts.append(part);continue
            location=part.get('image_url');value=location.get('url') if isinstance(location,dict) else None
            try:
                if not isinstance(value,str):raise ValueError('model_image_url')
                if prior:
                    old_values={m['content'][index]['image_url']['url'] for m in prior}
                    if value in old_values:
                        parts.append(part);continue
                    # Older SDK windows may retain the pre-384 source. Only
                    # accept the exact legacy encoded result as equivalence;
                    # source, text, media metadata and ordering also match.
                    legacy_value=await _limited(value,legacy=True)
                    if legacy_value in old_values:
                        parts.append({**part,'image_url':{**part['image_url'],'url':legacy_value}});continue
                if value not in memo:
                    details={}
                    if value.startswith('data:'):limited=await _limited(value,details)
                    else:
                        from media_egress import download_file
                        with tempfile.TemporaryDirectory(prefix='fishbot-model-image-') as folder:
                            path=Path(folder)/'source'
                            await download_file(value,path,max_bytes=POLICY['maxSourceBytes'])
                            raw=path.read_bytes()
                            # Detect MIME from bytes; do not trust remote extensions.
                            from PIL import Image
                            with Image.open(io.BytesIO(raw)) as image:mime=Image.MIME.get(image.format,'')
                            limited=await _limited('data:'+mime+';base64,'+base64.b64encode(raw).decode('ascii'),details)
                    memo[value]=(limited,details.get('animated',False))
                    # A later projection of this same request may already hold
                    # its model copy. Do not apply the source-size tiers twice.
                    memo.setdefault(limited,(limited,False))
                if memo[value][1]:
                    parts.append({'type':'text','text':'[动画已按时间顺序采样为3×3分帧预览；只据可见帧理解动作，不代表全部帧。]'})
                parts.append({**part,'image_url':{**part['image_url'],'url':memo[value][0]}})
            except asyncio.CancelledError:raise
            except Exception:parts.append({'type':'text','text':'[图片未传入：无法在尺寸或安全限制内处理；不要推测图片内容。]'})
        message['content']=parts;result.append(message)
    return result
