import {useEffect,useId,useRef,useState} from 'react';
import {createPortal} from 'react-dom';
import memoryDefaults from '../../../packages/contracts/memory-defaults.json';

const latest={id:'2026-11-18-referral-rewards',date:'2026年11月18日',title:'邀请好友，领取平台模型额度'};
function ReferralDetails(){return <><p>好友通过你的邀请链接并使用激活码完成注册后，你获赠 <strong>3,000 万 Token</strong>，好友获赠 <strong>2,000 万 Token</strong>。</p><p>在模型设置中选择「平台提供」即可使用，也可以随时查看剩余额度。</p></>}
function UpdateDetails(){return <><p>现在可以让机器人联网搜索、查阅网页并提供来源链接。B站能力也已强化，可以帮你查找视频、了解UP主、查询视频资料。</p><p>点赞、评论等账号操作需要先绑定 B站账号并开启对应授权。具体可用功能以机器人的当前设置和授权为准。</p><p>服务器此前意外断连，部分机器人需要重新登录 QQ。若机器人仍离线，请进入机器人的 QQ 登录页重新登录。</p><p>遇到问题请联系 QQ：<strong>2444309491</strong>。</p></>}
function PreviousUpdateDetails(){return <><p>我们正在优化机器人每次聊天时携带的信息：已经说过的内容尽量复用，只补充新消息和真正发生的变化，减少重复费用。</p><p><strong>近期测试场景的费用约为此前的七分之一。</strong>这是参考结果，不是每次聊天的固定折扣；实际费用会随模型、聊天长度、工具使用和缓存命中情况变化。</p><ul><li>默认每{memoryDefaults.summaryRounds}轮成功对话整理一次摘要和相关长期信息；表达学习仍按自己的节奏进行。</li><li>群聊每轮最多保留{memoryDefaults.groupRoundMessages}条消息，减少重复上下文。</li><li>人格、账号、已有记忆和真实工具权限继续保留。</li></ul></>}
export function Announcements(){return <section className="panel"><h1>公告</h1><article><small>{latest.date}</small><h2>{latest.title}</h2><ReferralDetails/><a href="#/referrals">前往邀请奖励</a></article><article><small>2026年11月17日</small><h2>新增联网搜索，B站能力升级</h2><UpdateDetails/></article><article><small>2026年11月6日</small><h2>聊天更省，记忆照常保留</h2><PreviousUpdateDetails/></article></section>}
function wasRead(key:string){try{return localStorage.getItem(key)==='read'}catch{return false}}
export function AnnouncementPopup({userId}:{userId:string}){
 const key='fishbot:announcement:'+userId+':'+latest.id;
 const [open,setOpen]=useState(()=>!wasRead(key)),ref=useRef<HTMLDialogElement>(null),label=useId();
 useEffect(()=>{if(!open)return;const previous=document.activeElement;ref.current?.showModal();return()=>{ref.current?.close();if(previous instanceof HTMLElement&&previous.isConnected)previous.focus()}},[open]);
 function close(){try{localStorage.setItem(key,'read')}catch{/* Dismiss still works when browser storage is unavailable. */}setOpen(false)}
 if(!open)return null;
 return createPortal(<dialog ref={ref} className="edit-dialog" aria-labelledby={label} onCancel={e=>{e.preventDefault();close()}}><small>更新公告 · {latest.date}</small><h2 id={label}>{latest.title}</h2><ReferralDetails/><div className="button-row"><a className="primary" href="#/referrals" onClick={close}>前往邀请奖励</a><button className="secondary" type="button" autoFocus onClick={close}>知道了</button></div></dialog>,document.body);
}
