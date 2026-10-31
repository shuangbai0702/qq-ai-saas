import type {BotConfig} from '../../../packages/contracts/index';
import {Field} from './ui';
type Props={config:BotConfig;onChange:(c:BotConfig)=>void};
export function RhythmControls({config,onChange}:Props){
 const b=config.behavior||{} as NonNullable<BotConfig['behavior']>;const update=(change:Partial<NonNullable<BotConfig['behavior']>>)=>onChange({...config,behavior:{...b,...change} as NonNullable<BotConfig['behavior']>});
 return <section className="panel"><h2>聊天节奏</h2><p>按回复字数安排打字等待，可按需要调整。分条间隔和打字等待取较大值，不重复叠加。</p>
 <label className="toggle-row"><div><b>自然打字停顿</b><small>短句快速发送，长句稍等；停止或更新机器人会取消等待中的旧回复。</small></div><input type="checkbox" aria-label="自然打字停顿" checked={!!b.typingSimulation} onChange={e=>update({typingSimulation:e.target.checked})}/></label>
 <div className="form-grid"><Field label="打字速度（字/秒）"><input type="number" min={5} max={60} disabled={!b.typingSimulation} value={b.typingSpeed??15} onChange={e=>update({typingSpeed:Number(e.target.value)})}/></Field><Field label="最长打字等待（秒）"><input type="number" min={0.25} max={5} step={0.25} disabled={!b.typingSimulation} value={b.typingMaxDelay??3} onChange={e=>update({typingMaxDelay:Number(e.target.value)})}/></Field></div>
 <label className="toggle-row"><div><b>轻微口语错字</b><small>只替换短中文闲聊中的少量语气词，不改数字、链接和代码；不需要可关闭。</small></div><input type="checkbox" aria-label="轻微口语错字" checked={!!b.typoEnabled} onChange={e=>update({typoEnabled:e.target.checked})}/></label>
 <Field label="错字触发概率" hint="参考0.02即2%，每条最多一个；不是完整拼音错字引擎。"><input type="number" min={0} max={0.05} step={0.01} disabled={!b.typoEnabled} value={b.typoProbability??0.02} onChange={e=>update({typoProbability:Number(e.target.value)})}/></Field>
 <label className="toggle-row"><div><b>被戳后偶尔回戳</b><small>只回应戳到自己的真实QQ事件，同会话60秒冷却，不额外调用模型。</small></div><input type="checkbox" aria-label="被戳后偶尔回戳" checked={!!b.pokeReply} onChange={e=>update({pokeReply:e.target.checked})}/></label>
 <label className="toggle-row"><div><b>话题注意力</b><small>群聊自动跟进时优先延续相关话题；轻量词语相关性判断，直接@和指令不受影响。默认开启；只做轻量词语匹配，模型还会结合当前群聊语境决定是否接话。</small></div><input type="checkbox" aria-label="话题注意力" checked={!!b.attentionEnabled} onChange={e=>update({attentionEnabled:e.target.checked})}/></label>
 <label className="toggle-row"><div><b>群聊疲劳退让</b><small>限制短时间连续自动接话，默认5分钟最多4次回复；直接@和私聊仍正常回应。</small></div><input type="checkbox" aria-label="群聊疲劳退让" checked={!!b.fatigueEnabled} onChange={e=>update({fatigueEnabled:e.target.checked})}/></label>
 <div className="form-grid"><Field label="疲劳统计窗口（秒）"><input type="number" min={60} max={1800} value={b.fatigueWindowSeconds??300} onChange={e=>update({fatigueWindowSeconds:Number(e.target.value)})}/></Field><Field label="窗口内回复次数"><input type="number" min={1} max={20} value={b.fatigueMaxReplies??4} onChange={e=>update({fatigueMaxReplies:Number(e.target.value)})}/></Field></div>
 <Field label="回戳概率" hint="参考0.25即25%。"><input type="number" min={0} max={1} step={0.05} disabled={!b.pokeReply} value={b.pokeProbability??0.25} onChange={e=>update({pokeProbability:Number(e.target.value)})}/></Field></section>;
}
export function QQPluginControls({config,onChange}:Props){
 const b=config.behavior||{} as NonNullable<BotConfig['behavior']>;const update=(change:Partial<NonNullable<BotConfig['behavior']>>)=>onChange({...config,behavior:{...b,...change} as NonNullable<BotConfig['behavior']>});
 return <><section className="panel"><h2>QQ 资料与空间</h2><p>主人在真实QQ聊天中明确要求时提供对应模型工具；可另开随心情更新签名。普通群友和内部主动任务无权操作。每Bot每天最多20次，不自动重试。</p>
 <Field label="角色主人QQ号" hint="与真实发送人核对；留空时以下工具均不可执行。"><input aria-label="角色主人QQ号" inputMode="numeric" maxLength={20} value={b.ownerUin||''} onChange={e=>update({ownerUin:e.target.value.replace(/\D/g,'')})}/></Field>
 {!b.ownerUin&&<div className="notice">尚未设置主人QQ号，资料和空间工具不会对任何人开放。</div>}
 <label className="toggle-row"><div><b>主人管理QQ资料</b><small>昵称、签名、常见在线状态；头像只能取当前附带或引用的QQ图片。</small></div><input type="checkbox" aria-label="主人管理QQ资料" checked={!!b.qqProfile} onChange={e=>update({qqProfile:e.target.checked})}/></label>
 <label className="toggle-row"><div><b>随心情更新签名</b><small>仅在主人夸奖、表达不满或提到生日的真实消息中允许模型顺手更新，最多40字；默认每日2次，不是定时修改。需要开启QQ资料管理。</small></div><input type="checkbox" aria-label="随心情更新签名" disabled={!b.qqProfile} checked={!!b.autoSignature} onChange={e=>update({autoSignature:e.target.checked})}/></label>
 <Field label="每日自动签名次数"><input type="number" min={0} max={2} value={b.dailyAutoSignatureLimit??2} onChange={e=>update({dailyAutoSignatureLimit:Number(e.target.value)})}/></Field>
 <label className="toggle-row"><div><b>主人要求时使用QQ空间</b><small>发纯文字说说，或看自己最近5条说说。不定时自动发，不自动点赞、评论或读取别人空间。</small></div><input type="checkbox" aria-label="主人要求时使用QQ空间" checked={!!b.qzone} onChange={e=>update({qzone:e.target.checked})}/></label>
 <Field label="说说可见范围" hint="默认好友可见，与参考插件公开发布不同。"><select aria-label="说说可见范围" disabled={!b.qzone} value={b.qzoneVisibility||'friends'} onChange={e=>update({qzoneVisibility:e.target.value as 'friends'|'self'|'public'})}><option value="friends">好友可见</option><option value="self">仅自己可见</option><option value="public">公开</option></select></Field>
 <p>QQ示例：“把昵称改成大肥鱼”“设置签名：今天也在摸鱼”“用这张图换头像”“发条说说：今天吃得很饱”“看看你自己的空间”。依赖模型工具调用和协议端支持；失败时不会宣称完成。</p></section>
 <section className="panel"><h2>主要插件对齐范围</h2><p>共享组采用平台受控实现，不直接加载有全局状态的原插件。</p><ul>
 <li>GroupChatPlus：插话、跟进、群氛围、分条、打字等待、轻微错字、回戳；未接完整注意力和密度算法。</li>
 <li>LivingMemory：人物事实、纠正/遗忘、摘要及事实/摘要语义召回；支持独立向量地址，完整知识图谱仍未等价。</li>
 <li>Meme Manager：图包、语境选图、授权收集审核；QQ收藏独立管理且默认关闭，没有自动识图。</li>
 <li>Proactive Chat：授权目标、持久等待、模型时间与话题规划、安静时段、未回复抑制；未接全部通知、日历扩展。</li>
 <li>QQProfile：主人要求时改资料，可选按主人心情话题更新签名，不随人格切换自动修改。</li>
 <li>Qzone：主人要求时发文字和看自己的空间；暂不含配图、评论、点赞。</li>
 </ul></section></>;
}
