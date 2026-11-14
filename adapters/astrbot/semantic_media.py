"""Owned original-image release after a committed semantic batch.

The SDK resolver's actual cleanup_paths and verified per-bot protocol cache
responses grant ownership. Never discover files from model text or directories.
Call retire_batch inside the successful summary transaction, then drain after
the replacement Journal retires the batch. A pending record survives a crash
between retirement and unlink.
"""
import asyncio,base64,hashlib,io,json,re,stat,time
from pathlib import Path


def image_key(value):return hashlib.sha256(value.encode('utf-8')).hexdigest()


from context_protocol import scoped_event_hash as event_key


def initialize(db):
    # execute rather than executescript: never commit a caller's transaction.
    for sql in (
        '''CREATE TABLE IF NOT EXISTS semantic_media_owned(
           path TEXT PRIMARY KEY,root TEXT NOT NULL,identity TEXT NOT NULL,
           state TEXT NOT NULL DEFAULT 'live',error TEXT NOT NULL DEFAULT '',updated REAL NOT NULL)''',
        '''CREATE TABLE IF NOT EXISTS semantic_media_images(
           image TEXT NOT NULL,path TEXT NOT NULL,PRIMARY KEY(image,path))''',
        '''CREATE TABLE IF NOT EXISTS semantic_media_refs(
           event TEXT NOT NULL,image TEXT NOT NULL,path TEXT NOT NULL,
           released INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(event,image,path))''',
        'CREATE INDEX IF NOT EXISTS semantic_media_refs_path ON semantic_media_refs(path,released)',
        '''CREATE TABLE IF NOT EXISTS semantic_media_retired_events(
           event TEXT PRIMARY KEY,checkpoint_id INTEGER NOT NULL)''',
        '''CREATE TABLE IF NOT EXISTS semantic_media_batches(
           checkpoint_id INTEGER PRIMARY KEY,images TEXT NOT NULL,events TEXT NOT NULL,
           untracked TEXT NOT NULL,created REAL NOT NULL)''',
        '''CREATE TABLE IF NOT EXISTS semantic_media_protocol_issues(
           event TEXT NOT NULL,file TEXT NOT NULL,reason TEXT NOT NULL,updated REAL NOT NULL,
           PRIMARY KEY(event,file))'''):
        db.execute(sql)


def _identity(file):
    info=file.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1:raise ValueError('original_not_owned_regular_file')
    # Hash also catches a same-path replacement on platforms with weak inode IDs.
    return json.dumps([info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns,
                       hashlib.sha256(file.read_bytes()).hexdigest()],separators=(',',':'))


def _contained(file,root):
    return file!=root and file.is_relative_to(root) and root.resolve()==root and file.resolve()==file and not file.is_symlink()


def owned_record(file,root,images,*,nested=False):
    """Create a record only for a resolver cleanup_path, before detach()."""
    file=Path(file).absolute();root=Path(root).resolve()
    # Only direct temp children are created by this pinned resolver. Resolving
    # through a symlink or accepting arbitrary nested directories grants no owner.
    if (not nested and file.parent!=root) or not _contained(file,root):
        raise ValueError('original_outside_owned_temp_root')
    return {'path':str(file),'root':str(root),'identity':_identity(file),
            'images':list(dict.fromkeys(image_key(value) for value in images))}


async def collect_protocol_originals(event,live,db=None,*,shared=False):
    """Ask this event's real OneBot for its raw image IDs, without DB awaits.

    NapCat get_image inherits GetFile and returns FileApi.downloadMedia's
    actual filePath. Its dataPath is this runner's isolated qq/profile. Only
    the account's nt_data/Pic and NapCat/temp subtrees are eligible; neither
    nt_db/login state nor global runtimes/sticker assets are image caches.
    """
    lease=event.get_extra('_hosted_lease');work=event.get_extra('_hosted_pipeline')
    if not isinstance(lease,dict) or not lease.get('root') or not work:return
    if db is not None and db.in_transaction:raise ValueError('media_admission_requires_idle_transaction')
    if event.get_extra('_hosted_media_protocol_checked'):return
    raw=getattr(event.message_obj,'raw_message',None)
    if not isinstance(raw,dict) or not isinstance(raw.get('message'),list):return
    account=str(event.get_self_id())
    if not re.fullmatch(r'\d{5,20}',account) or str(raw.get('self_id',''))!=account:return
    bot_root=Path(lease['root']).absolute()
    if bot_root.resolve()!=bot_root:raise ValueError('protocol_bot_root_changed')
    profile=bot_root/'qq'/'profile'
    roots=[profile/account/'nt_qq'/'nt_data'/'Pic',profile/'NapCat'/'temp']
    originals=list(event.get_extra('_hosted_media_originals') or []);issues=[];seen=set()
    from model_images import POLICY,_limited
    from PIL import Image
    for segment in raw['message']:
        if not isinstance(segment,dict) or segment.get('type') not in ('image','mface'):continue
        data=segment.get('data');file=data.get('file') if isinstance(data,dict) else None
        if not isinstance(file,str) or not file:
            issues.append({'file':image_key(json.dumps(segment,ensure_ascii=False,separators=(',',':'))),
                           'reason':'protocol_image_id_unavailable'});continue
        if file in seen:continue
        seen.add(file);key=image_key(file)
        try:
            if not live():raise asyncio.CancelledError('stale_protocol_image_request')
            result=await asyncio.wait_for(event.bot.get_image(file=file),10)
            if not live():raise asyncio.CancelledError('stale_protocol_image_request')
            if isinstance(result,dict) and isinstance(result.get('data'),dict):result=result['data']
            name=result.get('file') if isinstance(result,dict) else None
            if not isinstance(name,str) or not Path(name).is_absolute():raise ValueError('protocol_image_path_unavailable')
            path=Path(name).absolute()
            # Linux QQ uses a hashed account directory directly under this
            # runner's isolated profile. Derive only the returned path's exact
            # layout; do not discover or authorize other profile subtrees.
            candidates=list(roots)
            if path.is_relative_to(profile):
                parts=path.relative_to(profile).parts
                if len(parts)>3 and re.fullmatch(r'nt_qq_[0-9a-f]{32}',parts[0]) and parts[1:3]==('nt_data','Pic'):
                    candidates.append(profile/parts[0]/'nt_data'/'Pic')
            root=next((r for r in candidates if _contained(path,r)),None)
            if root is None:raise ValueError('protocol_image_outside_bot_cache')
            info=path.lstat()
            if not stat.S_ISREG(info.st_mode) or not 0<info.st_size<=POLICY['maxSourceBytes']:
                raise ValueError('protocol_image_size_or_type')
            # NapCat may report the transfer/thumbnail size while `file` names
            # the locally downloaded original (confirmed on Windows). The
            # isolated path, actual bounded stat, MIME and repeated byte hash
            # establish ownership; protocol metadata cannot veto that proof.
            raw_bytes=path.read_bytes()
            with Image.open(io.BytesIO(raw_bytes)) as image:
                mime=Image.MIME.get(image.format,'');image.verify()
            if not mime.startswith('image/'):raise ValueError('protocol_image_mime')
            value='data:'+mime+';base64,'+base64.b64encode(raw_bytes).decode('ascii')
            record=owned_record(path,root,[value],nested=True)
            if json.loads(record['identity'])[-1]!=hashlib.sha256(raw_bytes).hexdigest():
                raise ValueError('protocol_image_replaced')
            record['protocol_file']=key
            if db is not None:
                # Admit the real consumer before resizing yields. A concurrent
                # retired batch must not delete the shared original meanwhile.
                if db.in_transaction:raise ValueError('media_admission_requires_idle_transaction')
                scope='bot_shared' if shared else work.scope
                identifier=json.dumps([work.scope,str(work.event_id)],ensure_ascii=False,separators=(',',':')) if shared else str(work.event_id)
                with db:register_owned(db,[record],[{'scope':scope,'event':identifier,'images':[value]}])
            limited=await _limited(value)
            if not live():raise asyncio.CancelledError('stale_protocol_image_request')
            record=owned_record(path,root,[value,limited],nested=True)
            # Do not bind a replacement that arrived during decoding.
            if json.loads(record['identity'])[-1]!=hashlib.sha256(raw_bytes).hexdigest():
                raise ValueError('protocol_image_replaced')
            record['protocol_file']=key;originals.append(record)
        except asyncio.CancelledError:raise
        except Exception as error:issues.append({'file':key,'reason':str(error)[:120] if isinstance(error,ValueError) else type(error).__name__})
    event.set_extra('_hosted_media_originals',originals)
    event.set_extra('_hosted_media_protocol_issues',issues)
    event.set_extra('_hosted_media_protocol_checked',not bool(issues))


def register_event(db,event,materials):
    """Bind staged evidence and persist unresolved protocol mappings visibly."""
    initialize(db)
    work=event.get_extra('_hosted_pipeline')
    shared=any(m['scope']=='bot_shared' for m in materials)
    scope='bot_shared' if shared else work.scope
    identifier=json.dumps([work.scope,str(work.event_id)],ensure_ascii=False,separators=(',',':')) if shared else str(work.event_id)
    owner=event_key(scope,identifier)
    images={image_key(v) for m in materials if m['scope']==scope and m['event']==identifier for v in m.get('images',[])}
    originals=[];issues=list(event.get_extra('_hosted_media_protocol_issues') or [])
    for record in event.get_extra('_hosted_media_originals') or []:
        if record.get('protocol_file'):
            if not images.intersection(record['images']):
                issues.append({'file':record['protocol_file'],'reason':'protocol_image_not_in_captured_material'});continue
            db.execute('DELETE FROM semantic_media_protocol_issues WHERE event=? AND file=?',(owner,record['protocol_file']))
        originals.append(record)
    register_owned(db,originals,materials)
    for issue in issues:
        db.execute('INSERT INTO semantic_media_protocol_issues VALUES(?,?,?,?) ON CONFLICT(event,file) DO UPDATE SET reason=excluded.reason,updated=excluded.updated',
                   (owner,issue['file'],issue['reason'],time.time()))
    event.set_extra('_hosted_media_originals',[])


def register_owned(db,originals,materials):
    """Bind trusted staged resolver records to exact captured material events.

    Repeated projections may refer to existing originals without downloading
    again. Every still-live event using that image acquires its own reference.
    Retired events cannot be resurrected by an old append-only Journal prefix.
    """
    initialize(db)
    for record in originals:
        file=Path(record['path']);root=Path(record['root'])
        if not _contained(file,root) or _identity(file)!=record['identity']:
            raise ValueError('original_identity_changed_before_registration')
        prior=db.execute('SELECT identity,state FROM semantic_media_owned WHERE path=?',(str(file),)).fetchone()
        if prior and prior[0]!=record['identity']:
            if prior[1]=='deleted':
                # NapCat may download the same content-named cache file again
                # in a later batch. Its new physical incarnation has a new owner.
                db.execute('DELETE FROM semantic_media_refs WHERE path=?',(str(file),))
                db.execute('DELETE FROM semantic_media_images WHERE path=?',(str(file),))
                db.execute("UPDATE semantic_media_owned SET root=?,identity=?,state='live',error='',updated=? WHERE path=?",
                           (str(root),record['identity'],time.time(),str(file)))
            elif record.get('protocol_file') and json.loads(prior[0])[-1]==json.loads(record['identity'])[-1]:
                # The native client refreshed identical bytes. Preserve every
                # existing consumer, but fence deletion to this new identity.
                db.execute('UPDATE semantic_media_owned SET identity=?,updated=? WHERE path=?',
                           (record['identity'],time.time(),str(file)))
            else:raise ValueError('original_registry_path_reused')
        db.execute('INSERT OR IGNORE INTO semantic_media_owned(path,root,identity,updated) VALUES(?,?,?,?)',
                   (str(file),str(root),record['identity'],time.time()))
        for image in record['images']:
            db.execute('INSERT OR IGNORE INTO semantic_media_images VALUES(?,?)',(image,str(file)))
    for material in materials:
        event=event_key(material['scope'],material['event'])
        if db.execute('SELECT 1 FROM semantic_media_retired_events WHERE event=?',(event,)).fetchone():continue
        for value in material.get('images',[]):
            image=image_key(value)
            for (file,) in db.execute("SELECT i.path FROM semantic_media_images i JOIN semantic_media_owned o ON o.path=i.path WHERE i.image=? AND o.state<>'deleted'",(image,)).fetchall():
                db.execute('INSERT OR IGNORE INTO semantic_media_refs VALUES(?,?,?,0)',(event,image,file))
                db.execute("UPDATE semantic_media_owned SET state='live',error='',updated=? WHERE path=? AND state='pending'",(time.time(),file))


def retire_batch(db,material_images,checkpoint_id,event_hashes,*,scope=None):
    """Queue release in the caller's *successful, complete* batch transaction.

    event_hashes must use event_key(scope,event), including the canonical source
    tuple event string for bot_shared. scope is accepted for caller clarity;
    hashes already include it. This function does not commit or delete files.
    """
    if not db.in_transaction:raise ValueError('media_release_requires_summary_transaction')
    if type(checkpoint_id) is not int or checkpoint_id<=0:raise ValueError('invalid_media_checkpoint')
    initialize(db)
    images=sorted(set(image_key(value) for value in material_images));events=sorted(set(event_hashes))
    if any(not isinstance(e,str) or len(e)!=64 or any(c not in '0123456789abcdef' for c in e) for e in events):
        raise ValueError('invalid_media_event_hash')
    payload=(json.dumps(images),json.dumps(events))
    prior=db.execute('SELECT images,events,untracked FROM semantic_media_batches WHERE checkpoint_id=?',(checkpoint_id,)).fetchone()
    if prior:
        if prior[:2]!=payload:raise ValueError('media_batch_identity_changed')
        return {'untracked':len(json.loads(prior[2]))}
    untracked=[]
    for image in images:
        if not db.execute('SELECT 1 FROM semantic_media_images WHERE image=?',(image,)).fetchone():untracked.append(image)
    for event in events:
        # Capture can resize a registered original into a new model image hash.
        # Whole-event retirement releases every acquired original reference.
        db.execute('UPDATE semantic_media_refs SET released=1 WHERE event=?',(event,))
        db.execute('INSERT OR IGNORE INTO semantic_media_retired_events VALUES(?,?)',(event,checkpoint_id))
    db.execute('INSERT INTO semantic_media_batches VALUES(?,?,?,?,?)',
               (checkpoint_id,*payload,json.dumps(untracked),time.time()))
    db.execute("""UPDATE semantic_media_owned SET state='pending',updated=? WHERE state<>'deleted'
       AND EXISTS(SELECT 1 FROM semantic_media_refs r WHERE r.path=semantic_media_owned.path AND r.released=1)
       AND NOT EXISTS(SELECT 1 FROM semantic_media_refs r WHERE r.path=semantic_media_owned.path AND r.released=0)""",(time.time(),))
    return {'untracked':len(untracked)}


def drain(db):
    """Retry retired Journal releases; lock reference admission across unlink.

    A changed identity is a visible unresolved error, never permission to delete
    a replacement. Missing files are idempotent success after a prior unlink.
    """
    if db.in_transaction:raise ValueError('media_drain_requires_committed_batch')
    initialize(db);db.commit()
    result={'deleted':0,'pending':0,'errors':0,'protected':0,
            'protocol_unresolved':db.execute('SELECT count(*) FROM semantic_media_protocol_issues').fetchone()[0]}
    rows=db.execute("SELECT path FROM semantic_media_owned WHERE state='pending'").fetchall()
    for (name,) in rows:
        db.execute('BEGIN IMMEDIATE')
        try:
            row=db.execute("SELECT root,identity FROM semantic_media_owned WHERE path=? AND state='pending'",(name,)).fetchone()
            if not row:db.commit();continue
            if db.execute('SELECT 1 FROM semantic_media_refs WHERE path=? AND released=0',(name,)).fetchone():
                result['protected']+=1;db.commit();continue
            # Summary commit queues release before Journal replacement succeeds.
            # Every owner must have a durable retired batch, including owners
            # released by earlier checkpoints; missing evidence fails closed.
            batches=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='memory_semantic_batches'").fetchone()
            if not batches or db.execute('''SELECT 1 FROM semantic_media_refs r
               LEFT JOIN semantic_media_retired_events e ON e.event=r.event
               LEFT JOIN memory_semantic_batches b ON b.checkpoint_id=e.checkpoint_id
               WHERE r.path=? AND (b.state IS NULL OR b.state<>'retired')''',(name,)).fetchone():
                result['protected']+=1;db.commit();continue
            file=Path(name);root=Path(row[0])
            try:
                if not _contained(file,root):
                    raise ValueError('original_path_changed')
                try:identity=_identity(file)
                except FileNotFoundError:identity=None
                if identity is not None and identity!=row[1]:raise ValueError('original_identity_changed')
                if identity is not None:file.unlink()
                db.execute("UPDATE semantic_media_owned SET state='deleted',error='',updated=? WHERE path=?",(time.time(),name))
                result['deleted']+=1
            except (OSError,ValueError) as error:
                db.execute('UPDATE semantic_media_owned SET error=?,updated=? WHERE path=?',(type(error).__name__+':'+str(error)[:240],time.time(),name))
                result['errors']+=1
            db.commit()
        except BaseException:db.rollback();raise
    result['pending']=db.execute("SELECT count(*) FROM semantic_media_owned WHERE state='pending'").fetchone()[0]
    return result
