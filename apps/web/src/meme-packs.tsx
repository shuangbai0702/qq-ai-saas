import {useEffect,useRef,useState} from 'react';
import {api} from './api';
import type {BotConfig} from '../../../packages/contracts/index';
type Item={id:string;meaning:string;enabled:boolean;frames:number};
type Pack={id:'blue-fat-fish';name:string;ref:string;installed:boolean;source:string;version:string;items:Item[]};
export function MemePacks({botId,config,onChange}:{botId:string;config:BotConfig;onChange:(config:BotConfig)=>void}){
 const [pack,setPack]=useState<Pack|null>(null),[page,setPage]=useState(0),[notice,setNotice]=useState(''),[busy,setBusy]=useState(false);
 const owner=useRef<AbortController|null>(null),pending=useRef(false);
 useEffect(()=>{const controller=new AbortController();owner.current=controller;pending.current=false;setPack(null);setPage(0);setNotice('');setBusy(false);
  void api(`/bots/${botId}/meme-packs`,undefined,'GET',controller.signal).then(data=>{if(!controller.signal.aborted)setPack(data.packs[0])}).catch(e=>{if(!controller.signal.aborted)setNotice((e as Error).message)});
  return()=>controller.abort();
 },[botId]);
 async function toggle(item:Item){const controller=owner.current;if(!pack||!controller||controller.signal.aborted||pending.current)return;pending.current=true;setBusy(true);setNotice('');
  try{
   let err='';
   try{await api(`/bots/${botId}/meme-packs/blue-fat-fish/${item.id}`,{enabled:!item.enabled,expectedVersion:pack.version},'PATCH',controller.signal)}catch(e){err=(e as Error).message}
   const data=await api(`/bots/${botId}/meme-packs`,undefined,'GET',controller.signal);if(!controller.signal.aborted){setPack(data.packs[0]);setNotice(err||'已更新，无需重启。')}
  }catch(e){if(!controller.signal.aborted)setNotice((e as Error).message)}finally{if(!controller.signal.aborted){pending.current=false;setBusy(false)}}
 }
 const selected=(config.behavior?.memePacks||[]).includes('blue-fat-fish'),pages=Math.ceil((pack?.items.length||0)/12);
 return <section style={{marginTop:24}}><h4>动态大肥鱼图包</h4><label className="toggle-row"><div><b>{pack?.name||'蓝色大肥鱼表情包'}</b><small>{pack?.installed?`${pack.ref} · ${pack.items.length}张 · 已启用${pack.items.filter(i=>i.enabled).length}张`:'未安装或正在加载；不会用模拟素材代替。'}。与普通图库、群友收集库分开管理。</small></div><input aria-label="启用蓝色大肥鱼图包" type="checkbox" disabled={!pack?.installed} checked={selected} onChange={e=>onChange({...config,behavior:{...config.behavior,memePacks:e.target.checked?['blue-fat-fish']:[]} as NonNullable<BotConfig['behavior']>})}/></label>
 <p>图包开关需保存并应用。下方逐张启停即时生效；素材由模型按聊天语境选择，明确索要表情包不会用 QQ 小黄脸代替。</p>
 {pack?.source&&<a href={pack.source} target="_blank" rel="noreferrer">查看素材来源</a>}
 {pack?.installed&&<><div style={{display:'grid',gridTemplateColumns:'repeat(auto-fill,minmax(135px,1fr))',gap:16,marginTop:16}}>{pack.items.slice(page*12,page*12+12).map(item=><article key={item.id} style={{minWidth:0}}><img loading="lazy" src={`/v1/bots/${botId}/meme-packs/blue-fat-fish/${item.id}`} alt={item.meaning} style={{width:100,height:100,objectFit:'contain'}}/><p style={{overflowWrap:'anywhere',fontSize:13}}>{item.meaning}</p><button type="button" className="secondary" disabled={busy} onClick={()=>void toggle(item)}>{item.enabled?'停用此表情':'启用此表情'}</button></article>)}</div><div className="button-row"><button type="button" className="secondary" disabled={page===0} onClick={()=>setPage(p=>p-1)}>上一页</button><span>{page+1} / {pages}</span><button type="button" className="secondary" disabled={page+1>=pages} onClick={()=>setPage(p=>p+1)}>下一页</button></div></>}{notice&&<p role="status">{notice}</p>}</section>;
}
