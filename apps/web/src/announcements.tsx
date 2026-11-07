import {useEffect,useId,useRef,useState} from 'react';
import {createPortal} from 'react-dom';
import memoryDefaults from '../../../packages/contracts/memory-defaults.json';

const latest={id:'2026-11-06-context',date:'2026年11月6日',title:'聊天更省，记忆照常保留'};
function UpdateDetails(){return <><p>我们正在优化机器人每次聊天时携带的信息：已经说过的内容尽量复用，只补充新消息和真正发生的变化，减少重复费用。</p><p><strong>近期测试场景的费用约为此前的七分之一。</strong>这是参考结果，不是每次聊天的固定折扣；实际费用会随模型、聊天长度、工具使用和缓存命中情况变化。</p><ul><li>默认每{memoryDefaults.summaryRounds}轮成功对话整理一次摘要和相关长期信息；表达学习仍按自己的节奏进行。</li><li>群聊每轮最多保留{memoryDefaults.groupRoundMessages}条消息，减少重复上下文。</li><li>人格、账号、已有记忆和真实工具权限继续保留。</li></ul></>}
export function Announcements(){return <section className="panel"><h1>公告</h1><article><small>{latest.date}</small><h2>{latest.title}</h2><UpdateDetails/></article></section>}
function wasRead(key:string){try{return localStorage.getItem(key)==='read'}catch{return false}}
export function AnnouncementPopup({userId}:{userId:string}){
 const key='fishbot:announcement:'+userId+':'+latest.id;
 const [open,setOpen]=useState(()=>!wasRead(key)),ref=useRef<HTMLDialogElement>(null),label=useId();
 useEffect(()=>{if(!open)return;const previous=document.activeElement;ref.current?.showModal();return()=>{ref.current?.close();if(previous instanceof HTMLElement&&previous.isConnected)previous.focus()}},[open]);
 function close(){try{localStorage.setItem(key,'read')}catch{/* Dismiss still works when browser storage is unavailable. */}setOpen(false)}
 if(!open)return null;
 return createPortal(<dialog ref={ref} className="edit-dialog" aria-labelledby={label} onCancel={e=>{e.preventDefault();close()}}><small>更新公告 · {latest.date}</small><h2 id={label}>{latest.title}</h2><UpdateDetails/><div className="button-row"><a className="secondary" href="#/announcements" onClick={close}>查看公告</a><button className="primary" type="button" autoFocus onClick={close}>知道了</button></div></dialog>,document.body);
}
