"""Read-only installed packs plus per-bot exclusions. No remote URLs or models."""
import json, re
from pathlib import Path

PACK_ROOT=Path(__file__).resolve().parents[2]/'templates/meme-packs/blue-fat-fish'

def catalog(root, selected):
    if 'blue-fat-fish' not in (selected or []):return []
    try:
        entries=json.loads((PACK_ROOT/'index.json').read_text(encoding='utf8'))['items']
        settings=Path(root)/'meme-pack-settings.json'
        disabled=set(json.loads(settings.read_text(encoding='utf8')).get('disabled',[])) if settings.is_file() else set()
        result=[]
        for item in entries[:200]:
            identifier=item.get('id','')
            if not re.fullmatch(r'bf_[a-f0-9]{16}',identifier) or item.get('file')!=identifier+'.gif' or identifier in disabled:continue
            file=PACK_ROOT/item['file']
            if not file.is_file() or file.is_symlink() or file.stat().st_size>1048576:continue
            result.append({'id':identifier,'meaning':str(item.get('meaning',''))[:100],'category':str(item.get('category',''))[:30],'avoid':'严肃困难、隐私或真实冲突时不使用嘲讽或威胁类图片','path':str(file)})
        return result
    except (OSError,ValueError,KeyError,TypeError):return []
