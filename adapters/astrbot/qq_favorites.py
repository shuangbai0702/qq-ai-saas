"""Per-bot QQ favorites; account ID comes from the real QQ event, never text."""
import json,re
from pathlib import Path

def catalog(root,account):
 if not account:return []
 folder=Path(root)/'qq-favorites'
 try:
  index=json.loads((folder/'index.json').read_text(encoding='utf8'))
  if str(index.get('account',''))!=str(account) or not index.get('enabled',False):return []
  items=[]
  for item in index.get('items',[])[:200]:
   identifier=item.get('id','');file=item.get('file','')
   if not re.fullmatch(r'qqf_[a-f0-9]{16}',identifier) or file not in (identifier+'.gif',identifier+'.png') or not item.get('enabled') or not item.get('meaning'):continue
   image=folder/'images'/file
   if not image.is_file() or image.is_symlink() or image.stat().st_size>1048576:continue
   items.append({'id':identifier,'meaning':str(item['meaning'])[:100],'avoid':str(item.get('avoid',''))[:100],'path':str(image)})
  return items
 except (OSError,ValueError,KeyError,TypeError):return []
