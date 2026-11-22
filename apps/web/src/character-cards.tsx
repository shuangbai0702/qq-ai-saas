import {useEffect,useRef,useState} from 'react';
import {Plus,ArrowUp,ArrowDown,Trash2,Save} from 'lucide-react';
import {CharacterCardsSchema,type CharacterCard} from '../../../packages/contracts/character-cards';
import {api} from './api';
import {useLifetime} from './hooks';
import {CharacterCardLibrary} from './character-card-library';

const kinds={meme:'梗知识',speech:'说话示例',world:'世界背景',custom:'自定义'};
const hints={meme:'说明梗的含义、适用语境和使用方式。',speech:'填写场景与示例表达；这些是示例，不是真实聊天记录。',world:'填写世界背景、人物关系和适用范围。',custom:'填写你希望角色采用的补充设定。'};
export function CharacterCards({id,data,onDirtyChange,onSaved}:{id:string;data:any;onDirtyChange:(v:boolean)=>void;onSaved:()=>Promise<unknown>}){
 const [cards,setCards]=useState<CharacterCard[]>(()=>structuredClone(data.config.characterCards||[]));
 const [revision,setRevision]=useState<number>(data.bot.revision),[dirty,setDirty]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState(''),[ack,setAck]=useState<{revision:number;generation:number}|null>(null),[saved,setSaved]=useState(false),[applicationError,setApplicationError]=useState('');
 const [view,setView]=useState('library'),[libraryDirty,setLibraryDirty]=useState(false),[libraryNotice,setLibraryNotice]=useState('');
 const owner=useLifetime(),pending=useRef(false);
 useEffect(()=>{onDirtyChange(dirty||libraryDirty||busy);return()=>onDirtyChange(false)},[dirty,libraryDirty,busy,onDirtyChange]);
 useEffect(()=>{if(!dirty&&!pending.current&&data.bot.revision>revision){setCards(structuredClone(data.config.characterCards||[]));setRevision(data.bot.revision);setAck(null);setApplicationError('');setSaved(false)}},[data,dirty,revision]);
 function change(next:CharacterCard[]){setCards(next);setDirty(true);setError('')}
 function patch(index:number,value:Partial<CharacterCard>){change(cards.map((c,i)=>i===index?{...c,...value}:c))}
 function move(index:number,offset:number){const next=[...cards];[next[index],next[index+offset]]=[next[index+offset],next[index]];change(next)}
 async function save(){
  const page=owner.current;if(!page||page.signal.aborted||pending.current)return;
  const parsed=CharacterCardsSchema.safeParse(cards);if(!parsed.success){setError(parsed.error.issues[0].message);return}
  pending.current=true;setBusy(true);setError('');
  try{const result=await api(`/bots/${id}/character-cards`,{expectedRevision:revision,characterCards:cards},'PUT',page.signal);if(page.signal.aborted)return;setRevision(result.revision);setDirty(false);setSaved(true);if(!result.unchanged)setAck(result.applied?{revision:result.revision,generation:data.bot.generation}:null);setApplicationError(result.applicationError||'');await onSaved()}
  catch(e){if(!page.signal.aborted)setError((e as Error).message)}finally{if(!page.signal.aborted){pending.current=false;setBusy(false)}}
 }
 const failed=data.commands?.find((c:any)=>c.kind==='apply'&&c.status==='failed'&&c.idempotency_key===`config-${revision}`);
 const running=data.bot.desired_state==='running';
 const applied=running&&(ack?.revision===revision&&ack.generation===data.bot.generation||data.bot.health?.ai?.ready===true&&data.bot.applied_revision>=revision);
 const status=dirty?'有未保存修改':!running?'已保存，下次启动时应用':applied?'已生效':failed||applicationError?'应用失败':'已保存，等待应用';
 return <div className="cc-workspace"><div className="cc-workspace-tabs" role="tablist" aria-label="角色卡区域"><button type="button" role="tab" aria-selected={view==='library'} onClick={()=>setView('library')}>公共卡库</button><button type="button" role="tab" aria-selected={view==='installed'} onClick={()=>setView('installed')}>机器人装配 · {cards.length}/5{dirty?' *':''}</button></div>
 <div className="cc-view" hidden={view!=='library'}><CharacterCardLibrary installed={cards} onDirtyChange={setLibraryDirty} onAdd={card=>{const key='library-'+card.id;setCards(current=>current.length>=5||current.some(c=>c.id===key)?current:[...current,{id:key,name:card.name,kind:card.kind,content:card.content!,enabled:true}]);setDirty(true);setLibraryNotice('已添加当前版本副本。保存后生效；封面和描述不会发送给模型。');setView('installed')}}/></div>
 <div className="cc-view" hidden={view!=='installed'}><section className="panel character-cards" aria-label="角色卡管理"><div className="section-heading"><span className="eyebrow">CHARACTER CARDS / INSTALLED</span><h2>角色卡 <small>{cards.length}/5</small></h2><p>当前机器人的独立配置。启用卡片按顺序位于 note 与角色提示词之间；不会自动跟随公共卡库的编辑。</p></div>
 {libraryNotice&&<p className="notice">{libraryNotice}</p>}
  <div className="button-row"><span role="status">{status}{saved&&!dirty?` · v${revision}`:''}</span><span className="muted">启用正文合计 {cards.filter(c=>c.enabled).reduce((n,c)=>n+c.content.length,0).toLocaleString()} / 20,000 字符</span></div>
  {error&&<p className="alert" role="alert">{error}</p>}{applicationError&&!applied&&<p className="alert" role="alert">{applicationError}</p>}{failed&&!applied&&<p className="alert" role="alert">{failed.error||'运行实例应用失败，请检查运行状态后重试。'}</p>}
  <fieldset disabled={busy} style={{border:0,padding:0,minWidth:0}}>
   {!cards.length&&<p className="notice">还没有角色卡。选择一个类型，写下你自己的设定。</p>}
   {cards.map((card,index)=><article className="managed-item" key={card.id} aria-label={`角色卡 ${index+1}`}>
    <div className="button-row"><b>#{index+1} · {card.name||'未命名角色卡'}</b><label><input type="checkbox" aria-label={`启用角色卡 ${index+1}`} checked={card.enabled} onChange={e=>patch(index,{enabled:e.target.checked})}/> 启用</label><button className="secondary" type="button" aria-label={`上移角色卡 ${index+1}`} disabled={!index} onClick={()=>move(index,-1)}><ArrowUp size={15}/>上移</button><button className="secondary" type="button" aria-label={`下移角色卡 ${index+1}`} disabled={index===cards.length-1} onClick={()=>move(index,1)}><ArrowDown size={15}/>下移</button><button className="secondary" type="button" aria-label={`删除角色卡 ${index+1}`} onClick={()=>{if(confirm('删除这张角色卡？保存后生效。'))change(cards.filter(c=>c.id!==card.id))}}><Trash2 size={15}/>删除</button></div>
    <p className="muted" style={{overflowWrap:'anywhere'}}>{kinds[card.kind]} · {card.content.slice(0,100)||'正文待填写'}{card.content.length>100?'…':''}</p>
    <div className="form-grid"><label className="field"><span>名称</span><input aria-label={`角色卡 ${index+1} 名称`} maxLength={80} value={card.name} onChange={e=>patch(index,{name:e.target.value})}/></label><label className="field"><span>类型</span><select aria-label={`角色卡 ${index+1} 类型`} value={card.kind} onChange={e=>patch(index,{kind:e.target.value as CharacterCard['kind']})}>{Object.entries(kinds).map(([v,label])=><option key={v} value={v}>{label}</option>)}</select></label></div>
    <label className="field"><span>正文</span><small>{hints[card.kind]}</small><textarea aria-label={`角色卡 ${index+1} 正文`} rows={6} maxLength={4000} value={card.content} onChange={e=>patch(index,{content:e.target.value})}/><small>{card.content.length.toLocaleString()} / 4,000 字符</small></label>
   </article>)}
   <div className="button-row" aria-label="添加角色卡">{Object.entries(kinds).map(([kind,label])=><button className="secondary" key={kind} type="button" disabled={cards.length>=5} onClick={()=>change([...cards,{id:crypto.randomUUID(),name:label,kind:kind as CharacterCard['kind'],content:'',enabled:true}])}><Plus size={15}/>{label}</button>)}</div>
   {cards.length>=5&&<p className="muted">已达到 5 张上限，停用卡片也占名额；删除后可继续添加。</p>}
   <div className="button-row" style={{marginTop:20}}><button className="primary" type="button" disabled={!dirty||busy} onClick={()=>void save()}><Save size={16}/>{busy?'正在保存…':'保存并生效'}</button><button className="secondary" type="button" disabled={!dirty} onClick={()=>{if(confirm('放弃本页未保存修改？')){setCards(structuredClone(data.config.characterCards||[]));setRevision(data.bot.revision);setDirty(false);setError('')}}}>放弃修改</button></div>
  </fieldset><p className="muted">保存后，下一次尚未开始的模型请求使用新设定；正在回复的请求保持原设定。</p>
 </section></div></div>;
}
