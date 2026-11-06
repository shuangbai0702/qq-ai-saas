import {useEffect,useState} from 'react';
import {Search,X} from 'lucide-react';
const entries=[
 ['群聊白名单','config/chat','自主参与白名单群号','群号 群参与 自动搭话 旁听'],
 ['主动模式群白名单','autonomous','共用群参与白名单','外层 自主 群号'],
 ['机器人改名','config/profile','机器人名称','名字 名称 改名'],
 ['QQ昵称管理','config/plugins','QQ资料管理','改名字 QQ昵称 昵称 头像 资料'],
 ['主人身份','config/profile','角色主人的QQ号（可选）','认主 QQ 人格 角色'],
 ['模型连接','config/model','','API Key 地址 模型 思考强度'],
 ['接收与群参与','config/chat','','群消息 接话 艾特 @ 跟聊'],
 ['群聊连续跟进','config/reply','群聊连续跟进（秒）','跟聊 热聊 时间'],
 ['分条发送','config/reply','短句分条发送','气泡 回复 条数'],
 ['打字模拟','config/reply','自然打字停顿','停顿 速度 延迟'],
 ['旁听上下文','config/reply','群聊氛围消息数','记忆 上下文 消息数'],
 ['回复长度','config/reply','单次回复字数上限','字数 限制'],
 ['长期记忆','config/memory','持久化事实记忆','事实 记住 遗忘'],
 ['核心记忆便签','config/memory','AI核心记忆','便签 300字 核心'],
 ['摘要与压缩','config/memory','','摘要 压缩 轮数 大摘要'],
 ['亲密度与情绪','config/memory','','好感度 关系 人物 社交'],
 ['表达学习','config/memory','','语言 学习 口头禅'],
 ['平台表情包','config/images','','鱼动图 平台图片 表情包'],
 ['QQ收藏表情','config/images','','收藏 表情包 语义'],
 ['普通图库','config/gallery','','发图 图片 素材'],
 ['QQ内置表情','config/faces','允许QQ内置表情','小黄脸 原生表情'],
 ['标准模式主动联系','config/proactive','定时关心主人','找主人 主动私聊 定时'],
 ['群判断间隔','config/chat','群判断最短间隔（分钟）','冷却 群聊 判定'],
 ['主动模式','autonomous','主动行为模式','自主 外层 目标 休眠 唤醒'],
 ['自主思考间隔','autonomous','自主思考间隔（分钟）','主动模式 定时 休眠'],
 ['B站账号与工具','bilibili','启用B站工具','bilibili 登录 扫码 绑定'],
 ['B站合集与系列','bilibili','查看合集与系列','视频 分P 合集'],
 ['B站图片理解模型','bilibili','独立图片模型ID','视觉 看视频 模型'],
 ['QQ签名','config/plugins','随心情更新签名','自动签名 资料'],
 ['QQ空间','config/plugins','主人要求时使用QQ空间','动态 说说 发布'],
 ['QQ连接','qq','','扫码 登录 二维码 退出 重连'],
 ['运行记录','logs','','日志 报错 故障 回执'],
 ['能力地图','config/capabilities','','依赖 权限 功能 总览'],
] as const;
export function findCapabilities(query:string){const tokens=query.toLocaleLowerCase().trim().split(/\s+/).filter(Boolean);return tokens.length?entries.filter(e=>tokens.every(t=>e.join(' ').toLocaleLowerCase().includes(t))).slice(0,8):[]}
export function CapabilitySearch({id}:{id:string}){
 const [query,setQuery]=useState('');const results=findCapabilities(query);
 useEffect(()=>{
  let observer:MutationObserver|undefined,timer:ReturnType<typeof setTimeout>|undefined,highlight:HTMLElement|undefined;
  const clear=()=>{observer?.disconnect();clearTimeout(timer);highlight?.classList.remove('setting-search-highlight');observer=undefined;highlight=undefined};
  const locate=()=>{clear();if(!location.hash.startsWith('#/bots/'+id+'/'))return;const part=location.hash.split('/find/')[1];if(!part)return;let label:string;try{label=decodeURIComponent(part)}catch{return}
   const find=()=>{const control=Array.from(document.querySelectorAll<HTMLElement>('input,select,textarea')).find(el=>el.getAttribute('aria-label')===label||el.closest('label')?.querySelector('span,b')?.textContent?.trim()===label);if(!control)return false;const target=control.closest<HTMLElement>('label')||control;let parent=target.parentElement;while(parent){if(parent instanceof HTMLDetailsElement)parent.open=true;parent=parent.parentElement}target.classList.add('setting-search-highlight');highlight=target;target.scrollIntoView({block:'center',behavior:'auto'});control.focus({preventScroll:true});observer?.disconnect();return true};
   if(!find()){observer=new MutationObserver(()=>{find()});observer.observe(document.getElementById('main-content')||document.body,{subtree:true,childList:true})}timer=setTimeout(clear,5000);
  };
  addEventListener('hashchange',locate);locate();return()=>{removeEventListener('hashchange',locate);clear()};
 },[id]);
 return <section className="capability-search" aria-label="功能搜索"><div className="capability-search-input"><Search size={18}/><input type="search" aria-label="搜索机器人功能" placeholder="搜索功能，例如：群聊白名单、头像、记忆" value={query} onChange={e=>setQuery(e.target.value)}/>{query&&<button type="button" className="icon" aria-label="清空功能搜索" onClick={()=>setQuery('')}><X size={16}/></button>}</div>{query.trim()&&<div className="capability-search-results" aria-live="polite">{results.length?results.map(([title,route,label])=><a key={title} href={'#/bots/'+id+'/'+route+(label?'/find/'+encodeURIComponent(label):'')}><strong>{title}</strong><small>{route.startsWith('config/')?'配置':route==='autonomous'?'主动模式':route==='bilibili'?'Bilibili':'运行管理'}</small></a>):<p>未找到相关功能，请尝试“群聊”“记忆”“B站”等关键词。</p>}</div>}</section>
}
