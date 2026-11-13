"""Per-bot QQ favorites; account ID comes from the real QQ event, never text."""
import json,re,hashlib
from pathlib import Path


def confirmed_meaning(item):
 return bool(item.get('manual') or isinstance(item.get('semantic'),dict) and item['semantic'].get('confidence')=='clear')


def descriptions(root,account):
 """Same semantic authority for the live QQ tool and cached candidates.

 rawText is QQ's own text (modifyWord/desc/ocrWord); it is evidence about the
 source, never a confirmed meaning. Only enabled manual/confirmed-vision items
 carry a usable meaning.
 """
 try:
  data=json.loads((Path(root)/'qq-favorites'/'index.json').read_text(encoding='utf8'))
  if str(data.get('account',''))!=str(account):return {}
  return {i['id']:{'meaning':str(i.get('meaning',''))[:100],'avoid':str(i.get('avoid',''))[:100],'enabled':bool(i.get('enabled')) and confirmed_meaning(i),'rawText':str(i.get('rawText',''))[:100],'semanticStatus':str(i.get('semanticStatus') or ''),'manual':bool(i.get('manual'))} for i in data.get('items',[])[:200] if re.fullmatch(r'qqf_[a-f0-9]{16}',str(i.get('id','')))}
 except (OSError,ValueError,KeyError,TypeError):return {}


def description_id(row):
 return 'qqf_'+hashlib.sha256(str(row.get('md5') or row.get('resId') or row.get('url') or '').encode()).hexdigest()[:16]

def catalog(root,account):
 if not account:return []
 folder=Path(root)/'qq-favorites'
 try:
  index=json.loads((folder/'index.json').read_text(encoding='utf8'))
  if str(index.get('account',''))!=str(account) or not index.get('enabled',True):return []
  items=[]
  for item in index.get('items',[])[:200]:
   identifier=item.get('id','');file=item.get('file','')
   if not re.fullmatch(r'qqf_[a-f0-9]{16}',identifier) or file not in (identifier+'.gif',identifier+'.png') or not item.get('enabled') or not item.get('meaning') or not confirmed_meaning(item):continue
   image=folder/'images'/file
   if not image.is_file() or image.is_symlink() or image.stat().st_size>1048576:continue
   items.append({'id':identifier,'meaning':str(item['meaning'])[:100],'avoid':str(item.get('avoid',''))[:100],'path':str(image)})
  return items
 except (OSError,ValueError,KeyError,TypeError):return []
