import {useEffect,useId,useLayoutEffect,useRef,useState,type ComponentType} from 'react';
import {createPortal} from 'react-dom';
import {ArrowUpRight,Bell,ChevronRight,X} from 'lucide-react';
import memoryDefaults from '../../../packages/contracts/memory-defaults.json';
import './announcements.css';

function CharacterCardDetails(){
 return <>
  <p>打开机器人管理页，在「配置」左边选择「角色卡」，可以给机器人补充说话示例、知识和背景设定。</p>
  <ul>
   <li><strong>为机器人装配：</strong>每个机器人最多添加 5 张，支持编辑、排序和启停。运行中的机器人支持保存后热更新，应用进度可在页面查看。</li>
   <li><strong>在公共卡库创作：</strong>每个账号最多创作 3 张。可以选择表达、知识或背景，添加封面和描述。其他用户可以选用，只有作者能编辑原卡。</li>
  </ul>
  <p>从卡库选用时，会复制一份到当前机器人的草稿。保存后可自行编辑，原卡更新也不会覆盖你的版本。</p>
  <p>有问题或建议，欢迎加入 QQ 反馈群：<strong>176138627</strong>。</p>
 </>;
}

function LoginDetails(){
 return <>
  <p>近期腾讯加强了对 QQ 机器人的检测，导致部分机器人掉线。给大家带来不便，实在抱歉。</p>
  <p>遇到掉线，请打开该机器人的「QQ 登录」页面，重新扫码登录。若仍无法恢复，请通过反馈群联系我们。我们正在改进掉线恢复机制，感谢大家的理解。</p>
  <h3>邀请好友，领取平台模型额度</h3>
  <p>使用管理员直接发放的激活码注册后，可以邀请好友。好友通过邀请链接注册后，你获赠 <strong>3,000 万 Token</strong>，好友获赠 <strong>2,000 万 Token</strong>。</p>
  <p>在模型设置中选择「平台提供」即可使用，也可以随时查看剩余额度。</p>
 </>;
}

function SearchDetails(){
 return <>
  <p>现在可以让机器人联网搜索、查阅网页并提供来源链接。B站能力也已强化，可以帮你查找视频、了解UP主、查询视频资料。</p>
  <p>点赞、评论等账号操作需要先绑定 B站账号并开启对应授权。具体可用功能以机器人的当前设置和授权为准。</p>
  <p>服务器此前意外断连，部分机器人需要重新登录 QQ。若机器人仍离线，请进入机器人的 QQ 登录页重新登录。</p>
  <p>遇到问题请联系 QQ：<strong>2444309491</strong>。</p>
 </>;
}

function MemoryDetails(){
 return <>
  <p>我们正在优化机器人每次聊天时携带的信息：已经说过的内容尽量复用，只补充新消息和真正发生的变化，减少重复费用。</p>
  <p><strong>近期测试场景的费用约为此前的七分之一。</strong>这是参考结果，不是每次聊天的固定折扣；实际费用会随模型、聊天长度、工具使用和缓存命中情况变化。</p>
  <ul>
   <li>默认每{memoryDefaults.summaryRounds}轮成功对话整理一次摘要和相关长期信息；表达学习仍按自己的节奏进行。</li>
   <li>群聊每轮最多保留{memoryDefaults.groupRoundMessages}条消息，减少重复上下文。</li>
   <li>人格、账号、已有记忆和真实工具权限继续保留。</li>
  </ul>
 </>;
}

type Announcement={
 id:string;
 date:string;
 title:string;
 Details:ComponentType;
 action?:{href:string;label:string};
};

const announcements:readonly Announcement[]=[
 {id:'2026-11-23-character-cards',date:'2026-11-23',title:'角色卡与公共卡库上线',Details:CharacterCardDetails,action:{href:'#/bots',label:'前往我的机器人'}},
 {id:'2026-11-20-qq-session-and-referral',date:'2026-11-20',title:'QQ 掉线与重新登录说明 · 邀请奖励',Details:LoginDetails,action:{href:'#/referrals',label:'前往邀请奖励'}},
 {id:'2026-11-17-search',date:'2026-11-17',title:'新增联网搜索，B站能力升级',Details:SearchDetails},
 {id:'2026-11-06-memory',date:'2026-11-06',title:'聊天更省，记忆照常保留',Details:MemoryDetails},
];

const latest=announcements[0];

function AnnouncementDate({record}:{record:Announcement}){
 const [year,month,day]=record.date.split('-').map(Number);
 return <time dateTime={record.date}>{year}年{month}月{day}日</time>;
}

function AnnouncementReader({onNavigate}:{onNavigate?:()=>void}){
 const [selected,setSelected]=useState(latest.id);
 const current=announcements.find(record=>record.id===selected)??latest;
 const articleId=useId(),titleId=useId(),reader=useRef<HTMLElement>(null);
 const {Details,action}=current;

 function select(id:string){
  if(id===selected)return;
  setSelected(id);
  if(reader.current)reader.current.scrollTop=0;
 }

 return <div className="announcement-layout">
  <nav className="announcement-index" aria-label="公告列表">
   <ul>{announcements.map(record=><li key={record.id}>
    <button type="button" aria-controls={articleId} aria-pressed={record.id===selected} onClick={()=>select(record.id)}>
     <span className="announcement-index-title">{record.title}</span>
     <span className="announcement-index-date"><AnnouncementDate record={record}/>{record.id===latest.id&&<span className="announcement-new">最新</span>}</span>
     <ChevronRight size={15} aria-hidden="true"/>
    </button>
   </li>)}</ul>
  </nav>
  <article className="announcement-article" id={articleId} aria-labelledby={titleId} ref={reader} tabIndex={0}>
   <div className="announcement-article-heading" aria-live="polite">
    <AnnouncementDate record={current}/>
    <h2 id={titleId}>{current.title}</h2>
   </div>
   <div className="announcement-copy"><Details/></div>
   {action&&<a className="announcement-action" href={action.href} onClick={onNavigate}>{action.label}<ArrowUpRight size={16} aria-hidden="true"/></a>}
  </article>
 </div>;
}

export function Announcements(){
 return <section className="panel announcements-page">
  <h1 className="announcement-page-title"><Bell size={22} aria-hidden="true"/>公告</h1>
  <AnnouncementReader/>
 </section>;
}

function wasRead(key:string){
 try{return localStorage.getItem(key)==='read'}catch{return false}
}

export function AnnouncementPopup({userId,onDone}:{userId:string;onDone?:()=>void}){
 const key='fishbot:announcement:'+userId+':'+latest.id;
 const [open,setOpen]=useState(()=>!wasRead(key));
 const ref=useRef<HTMLDialogElement>(null),acknowledgeButton=useRef<HTMLButtonElement>(null),label=useId();

 useLayoutEffect(()=>{
  if(!open||!ref.current)return;
  const dialog=ref.current,previous=document.activeElement;
  dialog.showModal();
  acknowledgeButton.current?.focus({preventScroll:true});
  return ()=>{
   dialog.close();
   if(previous instanceof HTMLElement&&previous.isConnected)previous.focus();
  };
 },[open]);

 useEffect(()=>{if(!open)onDone?.()},[open,onDone]);

 function dismiss(){
  try{localStorage.setItem(key,'read')}catch{/* The dialog can still be dismissed without storage. */}
  setOpen(false);
 }

 if(!open)return null;
 return createPortal(<dialog ref={ref} className="edit-dialog announcement-dialog" aria-labelledby={label} onCancel={event=>{event.preventDefault();dismiss()}}>
  <header className="announcement-header">
   <span className="announcement-bell"><Bell size={19} aria-hidden="true"/></span>
   <h2 id={label}>更新公告</h2>
   <button className="icon announcement-close" type="button" aria-label="关闭公告" onClick={dismiss}><X size={20}/></button>
  </header>
  <AnnouncementReader onNavigate={dismiss}/>
  <div className="announcement-footer">
   <a href="#/announcements" onClick={dismiss}>查看全部公告<ArrowUpRight size={15} aria-hidden="true"/></a>
   <button className="primary" type="button" ref={acknowledgeButton} onClick={dismiss}>知道了</button>
  </div>
 </dialog>,document.body);
}
