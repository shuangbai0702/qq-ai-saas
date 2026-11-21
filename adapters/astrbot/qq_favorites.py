"""Per-bot QQ favorites; account ID comes from the real QQ event, never text."""
import json,re,hashlib
from pathlib import Path


def selection_text(item):
 if item.get('manual'):return str(item.get('meaning') or '')[:100]
 semantic=item.get('semantic')
 if not isinstance(semantic,dict) or semantic.get('confidence') not in ('clear','uncertain'):return ''
 if semantic.get('confidence')=='clear' and str(item.get('meaning') or semantic.get('meaning') or '').strip():return str(item.get('meaning') or semantic['meaning']).strip()[:100]
 description=str(semantic.get('description') or '').strip()
 return ('画面：'+description)[:100] if description else ''


def selection_avoid(item):
 semantic=item.get('semantic')
 return str(item.get('avoid') or '')[:100] if item.get('manual') or isinstance(semantic,dict) and semantic.get('confidence')=='clear' else ''


def descriptions(root,account):
 """Same semantic authority for the live QQ tool and cached candidates.

 rawText is QQ's own text (modifyWord/desc/ocrWord); it is evidence about the
 source, never a confirmed meaning. Enabled vision descriptions can be used
 without asserting an uncertain meme interpretation.
 """
 try:
  data=json.loads((Path(root)/'qq-favorites'/'index.json').read_text(encoding='utf8'))
  if str(data.get('account',''))!=str(account):return {}
  return {i['id']:{'meaning':selection_text(i),'avoid':selection_avoid(i),'enabled':bool(i.get('enabled')) and bool(selection_text(i)),'rawText':str(i.get('rawText',''))[:100],'semanticStatus':str(i.get('semanticStatus') or ''),'manual':bool(i.get('manual'))} for i in data.get('items',[])[:200] if re.fullmatch(r'qqf_[a-f0-9]{16}',str(i.get('id','')))}
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
   meaning=selection_text(item)
   if not re.fullmatch(r'qqf_[a-f0-9]{16}',identifier) or file not in (identifier+'.gif',identifier+'.png') or not item.get('enabled') or not meaning:continue
   image=folder/'images'/file
   if not image.is_file() or image.is_symlink() or image.stat().st_size>1048576:continue
   items.append({'id':identifier,'meaning':meaning,'avoid':selection_avoid(item),'path':str(image),'semantic_index_id':identifier,'semanticStatus':item.get('semanticStatus') or ('manual' if item.get('manual') else 'unknown')})
  return items
 except (OSError,ValueError,KeyError,TypeError):return []
