"""Import fixed upstream v1.0.0 archive into bounded, animated SaaS assets.
Originals remain in experiments; runtime only reads a chosen compressed GIF.
"""
import hashlib, io, json, re, sys, tarfile
from pathlib import Path
from PIL import Image, ImageSequence

archive = Path(sys.argv[1])
dest = Path('templates/meme-packs/blue-fat-fish')
dest.mkdir(parents=True, exist_ok=True)
prefix = 'blue-fat-fish-meme-pack-1.0.0/'
manifest = json.loads(Path('experiments/fish-alignment-evidence/blue-fish-manifest.json').read_text(encoding='utf8'))
items = []
with tarfile.open(archive, 'r:gz') as source:
    for member in source:
        if not member.isfile() or not member.name.startswith(prefix) or member.size > 16*1024*1024:
            continue
        relative = member.name[len(prefix):]
        parts = Path(relative).parts
        if relative in ('LICENSE', 'README.md'):
            (dest/relative).write_bytes(source.extractfile(member).read())
            continue
        if len(parts)!=3 or parts[0]!='memes' or '..' in parts or not relative.lower().endswith('.gif'):
            continue
        raw = source.extractfile(member).read()
        identifier = 'bf_'+hashlib.sha256(raw).hexdigest()[:16]
        if any(item['id']==identifier for item in items):
            continue
        original = Image.open(io.BytesIO(raw))
        # Sample across the complete animation and retain accumulated duration.
        count = original.n_frames
        indices = set(round(i*(count-1)/min(47,max(count-1,1))) for i in range(min(48,count)))
        frames, durations = [], []
        for i, frame in enumerate(ImageSequence.Iterator(original)):
            duration = max(20, min(10000, int(frame.info.get('duration',100))))
            if i in indices:
                thumb=frame.convert('RGBA');thumb.thumbnail((224,224))
                indexed=thumb.convert('RGB').quantize(colors=63)
                indexed.paste(255,mask=thumb.getchannel('A').point(lambda alpha:255 if alpha<128 else 0))
                indexed.info['transparency']=255
                frames.append(indexed)
                durations.append(duration)
            elif durations:
                durations[-1]+=duration
        output=io.BytesIO()
        frames[0].save(output,format='GIF',save_all=True,append_images=frames[1:],duration=durations,loop=0,optimize=False,disposal=2,transparency=255,background=255)
        encoded=output.getvalue()
        if len(encoded)>1048576:
            raise ValueError('Normalized GIF exceeds 1 MiB: '+relative)
        (dest/(identifier+'.gif')).write_bytes(encoded)
        category=parts[1]
        label=re.sub(r'^\d+[_\s-]*','',Path(parts[2]).stem)
        meaning=(label+'；'+manifest['categories'].get(category,{}).get('description',''))[:100]
        items.append(dict(id=identifier,file=identifier+'.gif',meaning=meaning,category=category,source=relative,bytes=len(encoded),frames=len(frames)))
        print(json.dumps({'processed':len(items),'bytes':len(encoded),'frames':len(frames)}),flush=True)
        original.close()
if len(items)!=157:
    raise ValueError('Expected upstream 157 unique images, got '+str(len(items)))
index=dict(id='blue-fat-fish',name=manifest['name'],source=manifest['homepage'],ref='v1.0.0',license=manifest['license'],archiveSHA256=hashlib.file_digest(archive.open('rb'),'sha256').hexdigest(),normalization='224px, up to 48 frames, 63 colors plus transparency; animation duration retained',items=items)
(dest/'index.json').write_text(json.dumps(index,ensure_ascii=False,indent=2),encoding='utf8')
print(json.dumps({'complete':True,'count':len(items),'bytes':sum(i['bytes'] for i in items)}),flush=True)
