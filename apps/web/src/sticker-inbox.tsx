import {useEffect,useRef,useState} from 'react';
import type {BotConfig} from '../../../packages/contracts/index';
import {useLifetime,useLiveResource} from './hooks';
import {api} from './api';
import {EditDialog} from './edit-dialog';

export function StickerInbox({id,config,onChange}:{id:string;config:BotConfig;onChange:(c:BotConfig)=>void}){
 const {data,error,refresh}=useLiveResource('/bots/'+id+'/stickers',0),owner=useLifetime(),pending=useRef(false);
 const [editing,setEditing]=useState<any>(),[busy,setBusy]=useState(false),[notice,setNotice]=useState('');
 const b:Partial<NonNullable<BotConfig['behavior']>>=config.behavior||{},update=(value:object)=>onChange({...config,behavior:{...b,...value} as NonNullable<BotConfig['behavior']>});
 const groupValue=(b.stickerGroups||[]).join(','),lastGroups=useRef(groupValue),[groupText,setGroupText]=useState(groupValue);
 useEffect(()=>{if(lastGroups.current!==groupValue){lastGroups.current=groupValue;setGroupText(groupValue)}},[groupValue]);
 async function review(item:any,status:string,meaning=''){
  const page=owner.current;if(!page||page.signal.aborted||pending.current)throw new Error('操作尚未结束');pending.current=true;setBusy(true);setNotice('');
  try{await api('/bots/'+id+'/stickers/'+item.id,{status,meaning,expectedVersion:item.version},'PATCH',page.signal);if(page.signal.aborted)return;
   setNotice('操作已保存。审核通过的表情仅可在来源群使用。');const latest=await refresh();if(!latest&&!page.signal.aborted)throw new Error('操作已提交，但刷新失败；请关闭编辑并刷新列表，不要重复提交。');
  }finally{if(!page.signal.aborted){pending.current=false;setBusy(false)}}
 }
 return <section className="panel"><div className="section-heading"><h2>从QQ聊天收集表情包</h2><p>只收机器人允许群中QQ协议标记的表情图片，不读取手机QQ收藏，也不抓取普通照片。收图无需@，发送仍遵守聊天触发规则。</p></div>
 <label className="toggle-row"><div><b>自动收集聊天表情包</b><small>推荐开启，先进入待审核区。每机器人最多10张待审核、20张已审核；每分钟最多一次下载。GIF和WebP暂转为首帧PNG，不保留动画。</small></div><input type="checkbox" checked={!!b.stickerCollect} onChange={e=>update({stickerCollect:e.target.checked})}/></label>
 <label className="toggle-row"><div><b>从机器人允许的全部群收集</b><small>仍遵守机器人群聊白名单。关闭后只收下方指定群。</small></div><input type="checkbox" checked={!!b.stickerAllGroups} onChange={e=>update({stickerAllGroups:e.target.checked})}/></label><label className="field"><span>允许收集的群号</span><input disabled={!!b.stickerAllGroups} value={groupText} placeholder="多个群号用逗号分隔" onChange={e=>{const value=e.target.value,groups=[...new Set(value.split(/[,，\s]+/).filter(Boolean))];setGroupText(value);lastGroups.current=groups.join(',');update({stickerGroups:groups})}}/><small>指定群模式下留空不收集；开启全部群后无需填写。修改开关与群号后须保存并应用。</small></label>
 <label className="field"><span>每日收图尝试上限</span><input type="number" min={0} max={30} value={b.dailyStickerCollectLimit??10} onChange={e=>update({dailyStickerCollectLimit:Number(e.target.value)})}/><small>0停止收集；失败或重复下载也占用额度，每天北京时间08:00刷新。不调用视觉模型。</small></label>
 {b.stickerCollect&&!b.stickerAllGroups&&!b.stickerGroups?.length&&<p className="dependency-note">尚未添加授权群，暂不会收图。</p>}
 {b.stickerCollect&&!b.memeEnabled&&<p className="dependency-note">当前只收集，聊天表情包发送开关尚未开启。审核记录会保留，开启发送后才用于回复。</p>}
 <div className="button-row"><h3>待审核与已收录表情</h3><button type="button" className="secondary" disabled={busy} onClick={()=>void refresh()}>刷新收集列表</button></div>
 {(error||notice)&&<p role="status">{error||notice}</p>}
 {!data?<p>{error?'读取失败，请刷新收集列表重试。':'正在读取收集列表…'}</p>:!data.items.length?<p>尚无收集记录。先开启收集并授权群，再接收群友发送的表情。</p>:<div className="collected-stickers">{data.items.map((item:any)=><article className="managed-item" key={item.id}><img src={`/v1/bots/${id}/stickers/${item.id}`} alt={item.meaning||'待审核表情'} style={{width:96,height:96,objectFit:'contain'}}/><b>{item.status==='approved'?'已收录':'待审核'} · {item.meaning||'尚未填写适用场景'}</b><small>来源群 {item.scope.split(':')[1]} · 发送者 {item.subject} · {new Date(item.created*1000).toLocaleString()}</small><div className="button-row"><button type="button" className="secondary" disabled={busy} onClick={()=>setEditing(item)}>{item.status==='approved'?'修改表情场景':'审核收录'}</button><button type="button" className="secondary" disabled={busy} onClick={()=>{if(confirm('移除这个表情？已收录表情将停止用于后续回复，同一来源群的相同内容不会立刻再收集。'))void review(item,'rejected').catch(e=>{if(!owner.current?.signal.aborted)setNotice(e.message)})}}>移除表情</button></div></article>)}</div>}
 {editing&&<EditDialog title="审核聊天表情包" fields={[{key:'meaning',label:'这张表情适合什么场景',value:editing.meaning,maxLength:100,required:true}]} onClose={()=>setEditing(undefined)} onSave={values=>review(editing,'approved',values.meaning)}/>}
 </section>;
}
