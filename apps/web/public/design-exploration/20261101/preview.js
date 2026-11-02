/* Local design study. All state is illustrative and stays in this page. No API calls. */
const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const icons = {
  fish: '<path d="M6 12c2-4 5-6 9-6l4 6-4 6c-4 0-7-2-9-6Z"/><path d="m6 12-4-4v8l4-4Zm8-3h.01"/>',
  grid: '<rect x="3" y="3" width="7" height="7" rx="2"/><rect x="14" y="3" width="7" height="7" rx="2"/><rect x="3" y="14" width="7" height="7" rx="2"/><rect x="14" y="14" width="7" height="7" rx="2"/>',
  spark: '<path d="m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5L12 3Z"/>',
  activity: '<path d="M3 12h4l3-8 4 16 3-8h4"/>',
  arrow: '<path d="M5 12h14m-6-6 6 6-6 6"/>',
  arrowup: '<path d="M6 18 18 6M6 6h12v12"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  search: '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 4 4"/>',
  chevron: '<path d="m9 5 7 7-7 7"/>',
  down: '<path d="m6 9 6 6 6-6"/>',
  check: '<path d="m5 12 4 4L19 6"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  shield: '<path d="m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6l8-3Z"/><path d="m8 12 3 3 5-6"/>',
  book: '<path d="M12 6c-3-2-6-2-9-1v14c3-1 6-1 9 1 3-2 6-2 9-1V5c-3-1-6-1-9 1Zm0 0v14"/>',
  message: '<path d="M21 11a8 8 0 0 1-8 8H5l-3 3V11a9 9 0 0 1 19 0Z"/><path d="M7 10h10M7 14h6"/>',
  settings: '<path d="M4 7h16M4 17h16"/><circle cx="8" cy="7" r="3"/><circle cx="16" cy="17" r="3"/>',
  server: '<rect x="3" y="3" width="18" height="7" rx="2"/><rect x="3" y="14" width="18" height="7" rx="2"/><path d="M7 6.5h.01M7 17.5h.01M11 6.5h6M11 17.5h6"/>',
  close: '<path d="m6 6 12 12M18 6 6 18"/>',
  heart: '<path d="M20.8 4.6a5.5 5.5 0 0 0-7.8 0L12 5.7l-1.1-1.1a5.5 5.5 0 0 0-7.8 7.8L12 21l8.8-8.6a5.5 5.5 0 0 0 0-7.8Z"/>',
  moon: '<path d="M20.7 13a9 9 0 0 1-9.7-9.7A9 9 0 1 0 20.7 13Z"/>',
  help: '<circle cx="12" cy="12" r="9"/><path d="M9 9a3 3 0 0 1 6 0c0 2-3 2-3 4m0 4h.01"/>',
};
const icon = (name, cls = '') => `<svg class="icon ${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.65" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${icons[name] || icons.fish}</svg>`;
const escape = value => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const themes = {
  tidal: {number:'01', name:'潮汐工作室', en:'TIDAL ATELIER', tagline:'把日常交给鱼，把时间留给你。', description:'清透的蓝，刚刚好的秩序。', hello:'下午好，双白', note:'海风正好，鱼也在线。', design:'蓝白侧栏 / 连续工作面 / 轻盈反馈'},
  paper: {number:'02', name:'鱼信事务所', en:'THE FISH POST', tagline:'今天，也有一条鱼在等你。', description:'暖纸与墨色，把管理变成日常。', hello:'双白，见字如晤', note:'今日鱼信，已经为你收好。', design:'暖纸横栏 / 名片陈列 / 编辑式字阶'},
  midnight: {number:'03', name:'深蓝指挥舱', en:'MIDNIGHT CURRENT', tagline:'万家灯火里，你的鱼一直在。', description:'深海蓝底色，安静看清每一个状态。', hello:'欢迎回来，双白', note:'一切有序，随时可以开始。', design:'深蓝窄栏 / 信息分区 / 高对比数据'},
};
const bots = [
  {id:'fish', name:'我的大肥鱼', persona:'大肥鱼', qq:'1086 ••• 203', state:'online', status:'QQ 在线', mood:'有点开心', model:'Gemini Flash', color:'blue', initial:'鱼', pss:'48.6', swap:'92.4', time:'2 分钟前', note:'刚刚在群里接上了话', activity:'接上了一个正在聊的话题', memory:'238 条', autonomy:'群聊接话 · 主人联系'},
  {id:'moon', name:'月亮来信', persona:'温柔陪伴', qq:'2941 ••• 618', state:'online', status:'QQ 在线', mood:'平静', model:'Gemini Flash', color:'peach', initial:'月', pss:'46.2', swap:'88.1', time:'8 分钟前', note:'正在安静陪伴', activity:'保存了一段新的聊天记忆', memory:'126 条', autonomy:'主人联系'},
  {id:'cloud', name:'云朵观察员', persona:'随性闲聊', qq:'3067 ••• 092', state:'offline', status:'离线 · 已清退', mood:'休息中', model:'Gemini Flash', color:'sage', initial:'云', pss:'—', swap:'—', time:'昨天', note:'配置和登录会话已保留', activity:'离线资源已释放', memory:'57 条', autonomy:'群聊接话'},
];
const state = {theme:'tidal', page:'robots', filter:'all', search:'', monitor:'machine', motion:true, settings:{groupInterval:'10',ownerInterval:'30',ownerIdle:'10',ownerLimit:'10',split:'adaptive',gap:'800'}, flags:{memory:true, emotion:true, images:true, group:true, owner:true, quiet:true}};
let toastTimer;
const app = $('#app');
function notify(text) {
  clearTimeout(toastTimer);
  const toast = $('.toast'); toast.textContent = text; toast.classList.add('visible');
  toastTimer = setTimeout(() => toast.classList.remove('visible'), 3000);
}
function avatar(bot, size = '') {
  return `<span class="bot-avatar ${bot.color} ${size}">${bot.id === 'fish' ? '<img src="/illustrations/dafeiyu-rice.png" alt="">' : `<span>${bot.initial}</span>`}</span>`;
}
function status(bot) { return `<span class="status ${bot.state}"><i></i>${bot.status}</span>`; }
function nav() {
  return `<a href="#${state.theme}/robots" class="product-brand">${icon('fish')}<span>FishBot<small>让陪伴，自然发生</small></span></a>
  <div class="workspace-chip"><span class="workspace-avatar">双</span><span>双白的工作区<small>个人空间 · 管理员</small></span>${icon('down')}</div>
  <span class="nav-caption">工作空间</span><nav class="product-nav" aria-label="工作区页面">${[['robots','grid','我的机器人'],['abilities','spark','能力与表达'],['resources','activity','资源与运行']].map(([id,ic,label])=>`<a href="#${state.theme}/${id}" class="${state.page===id?'active':''}" ${state.page===id?'aria-current="page"':''}>${icon(ic)}<span>${label}</span>${state.page===id?'<i></i>':''}</a>`).join('')}</nav>
  <div class="nav-bottom"><div class="workspace-health">${icon('shield')}<span>独立的记忆，相同的安心<small>每条鱼的身份和数据分别保存</small></span></div><button class="help-button" data-action="help">${icon('help')}<span>使用指南</span>${icon('arrowup')}</button><div class="account"><span class="user-avatar">双</span><span>双白<small>个人工作区</small></span><span class="account-dot"></span></div></div>`;
}
function header() {
  const titles = {robots:'我的机器人', abilities:'能力与表达', resources:'资源与运行'};
  return `<header class="workspace-top"><span><b>工作空间</b>${icon('chevron')}<span>${titles[state.page]}</span></span><div class="top-right"><span class="system-health"><i></i>服务运行正常</span><span class="top-date">11 月 01 日 · 星期日</span><button class="icon-button motion-control" data-action="motion" aria-label="${state.motion?'关闭':'开启'}动效" title="${state.motion?'关闭':'开启'}轻动效">${icon(state.motion?'spark':'moon')}</button></div></header>`;
}
function title(eyebrow, heading, sub, action = '') {
  return `<div class="page-heading"><div><div class="eyebrow">${eyebrow}</div><h1>${heading}</h1><p>${sub}</p></div>${action}</div>`;
}
function trendLine(kind = 'smooth', filled = false) {
  const path = kind === 'ram' ? 'M0 38 C20 42 30 15 52 24 S88 64 110 48 S145 46 164 38 S195 24 216 37 S243 40 270 29 S305 34 330 25 S370 31 400 22' : kind === 'swap' ? 'M0 52 C40 50 50 40 80 42 S128 36 154 36 S195 26 224 31 S278 27 300 22 S350 24 400 17' : 'M0 50 C20 50 28 33 48 39 S70 55 94 42 S125 48 144 31 S175 42 195 27 S228 39 249 26 S277 36 300 21 S340 29 356 17 S385 21 400 10';
  return `<svg class="sparkline ${filled?'filled':''}" viewBox="0 0 400 72" preserveAspectRatio="none" aria-hidden="true">${filled?`<path class="area" d="${path} L400 72 H0Z"/>`:''}<path d="${path}"/></svg>`;
}
function overview() {
  return `<section class="hero panel"><div class="hero-content"><div class="eyebrow"><span class="tiny-sun">✳</span>${themes[state.theme].en} · YOUR EVERYDAY COMPANION</div><h2>${themes[state.theme].hello}<span>。</span></h2><p>${themes[state.theme].tagline}</p><div class="hero-footer"><span><i class="live-dot"></i>2 条鱼在线，陪伴正在发生</span><button data-page="abilities">看看它们的能力 ${icon('arrow')}</button></div></div><div class="hero-art"><div class="orbit orbit-one"></div><div class="orbit orbit-two"></div><div class="art-caption"><span>DAFEIYU</span><i>just being here.</i></div><img src="/illustrations/dafeiyu-rice.png" alt="大肥鱼捧着饭碗"/><span class="art-sticker">${icon('heart')}在，好好吃饭呢</span></div></section>
  <section class="metrics" aria-label="工作区概览"><article class="metric panel"><div class="metric-label">${icon('fish')}我的机器人<span>01</span></div><div class="metric-value">3 <small>条</small></div><div class="metric-foot">独立人格，分别保存记忆<span>3 / 5 名额</span></div><span class="metric-mark">≈</span></article><article class="metric panel"><div class="metric-label">${icon('activity')}真正在线 QQ<span>02</span></div><div class="metric-value">2 <small>个</small><span class="metric-indicator">连接正常</span></div><div class="metric-foot">按实际协议连接统计<span class="good-text">● 正常</span></div>${trendLine()}</article><article class="metric panel"><div class="metric-label">${icon('spark')}共享 AI 运行时<span>03</span></div><div class="metric-value">1 <small>个进程</small></div><div class="metric-foot">2 个在线成员 · PSS 36.8 MiB<span>就绪</span></div><span class="metric-mark">✧</span></article></section>`;
}
function botCard(bot) {
  return `<article class="bot-row panel ${bot.state}" data-bot="${bot.id}"><div class="bot-identity">${avatar(bot)}<div><h3><button data-open="${bot.id}">${bot.name}</button></h3><p>${bot.persona} <span>· QQ ${bot.qq}</span></p></div></div><div class="bot-model"><b>${bot.model}</b><span>共享 AI ${bot.state==='online'?'<i class="ready-dot"></i>已就绪':'· 按需启动'}</span></div><div class="bot-state">${status(bot)}<span>${bot.state==='online'?bot.note:'进程已释放 · 数据已保留'}</span></div><div class="bot-actions"><button class="button secondary small" data-open="${bot.id}">${bot.state==='online'?'管理':'连接 QQ'}${icon(bot.state==='online'?'arrowup':'arrow')}</button></div><div class="bot-card-footer"><span>${icon('clock')}${bot.time}</span><span>${bot.state==='online'?`<span class="mood-dot"></span>${bot.mood}`:'下次启动可恢复登录'}</span></div></article>`;
}
function botList() {
  const matches = bots.filter(bot => (state.filter === 'all' || bot.state === state.filter) && [bot.name, bot.persona, bot.qq].join(' ').includes(state.search.trim()));
  return `<div class="list-header"><div><span class="eyebrow">YOUR LITTLE COMPANIONS</span><h2>每条鱼，都有自己的日常<span>03</span></h2></div><label class="search">${icon('search')}<input id="search" placeholder="找一条鱼…" aria-label="搜索机器人" value="${escape(state.search)}" autocomplete="off"><kbd>/</kbd></label></div><div class="filter-row"><div class="segmented" role="group" aria-label="按连接状态筛选">${[['all','全部',3],['online','在线',2],['offline','离线',1]].map(([id,label,count])=>`<button data-filter="${id}" aria-pressed="${state.filter===id}">${label}<span>${count}</span></button>`).join('')}</div><span class="list-hint">状态与 AI 就绪分开显示</span></div><div class="roster-labels"><span>机器人 / 人格</span><span>模型与运行方式</span><span>QQ 连接</span><span>操作</span></div><div class="bot-rows">${matches.length?matches.map(botCard).join(''):'<div class="empty panel">没找到这条鱼。<button class="text-button" data-action="clear">清除搜索与筛选</button></div>'}</div>`;
}
function rightRail() {
  return `<aside class="right-rail"><section class="daily-note panel"><div class="section-mini-head"><span class="eyebrow">一封今日鱼信</span>${icon('book')}</div><div class="note-date">NOV <strong>01</strong><span>SUNDAY</span></div><h3>陪伴，有自己的节奏。</h3><p>有人说话时认真接住，<br>安静时也不急着打扰。</p><div class="note-signature">${icon('fish')}大肥鱼 · 在你身边</div></section><section class="activity-panel panel"><div class="section-mini-head"><h3>最近的小事</h3><span class="subtle-dot"></span></div><ol class="timeline">${bots.map(bot=>`<li><span class="timeline-dot ${bot.color}"></span><div><b>${bot.name}</b><p>${bot.activity}</p><time>${bot.time}</time></div></li>`).join('')}</ol><button class="text-button" data-page="resources">看看运行状态 ${icon('arrow')}</button></section><p class="privacy-note">${icon('shield')}账号、Key 与聊天记忆按机器人隔离</p></aside>`;
}
function robotsPage() {
  return `${title('YOUR WORKSPACE / 01','我的机器人','把连接、人格和运行状态，放在一个清楚的地方。',`<button class="button primary" data-action="create">${icon('plus')}创建机器人</button>`)}${overview()}<div class="dashboard-grid"><section class="roster">${botList()}<div class="roster-foot">${icon('shield')}离线只释放运行资源，账号、配置和记忆继续保留。<span>随时回来，继续聊天。</span></div></section>${rightRail()}</div>`;
}
function toggle(key, label) {return `<button class="toggle ${state.flags[key]?'on':''}" role="switch" aria-checked="${state.flags[key]}" aria-label="${label}" data-toggle="${key}"><span></span></button>`;}
function abilityRow(key, ic, name, text) {return `<div class="ability-row"><span class="ability-icon">${icon(ic)}</span><div><h3>${name}</h3><p>${text}</p></div>${toggle(key,name)}</div>`;}
function abilitiesPage() {
  return `${title('PERSONALITY & PRESENCE / 02','像它自己，自然地相处','先定义它是谁，再决定它如何记住、表达和主动靠近。',`<button class="button primary" data-action="save">${icon('check')}保存预览设置</button>`)}<div class="selected-bot panel">${avatar(bots[0],'mini')}<div><b>我的大肥鱼</b><small>正在调整这条鱼的能力</small></div><span class="status online"><i></i>QQ 在线</span><span class="preview-local">仅作用于本页预览</span></div>
  <div class="ability-layout"><div class="ability-main"><section class="ability-section panel"><div class="ability-section-head"><span class="section-number">01</span><div><span class="eyebrow">FOUNDATION</span><h2>它是谁，记得什么</h2><p>人格、身份和记忆，是日常相处的底色。</p></div><span class="level-tag">基础能力</span></div><div class="persona-preview"><span class="persona-quote">“</span><p>有自己的喜好，认真接住聊天，也保留一点小脾气。</p><span>大肥鱼人格</span></div>${abilityRow('memory','book','共同记忆','群聊与私聊可召回同一人的往事，权限与身份分别校验。')}${abilityRow('emotion','heart','情绪与喜好','让近期相处影响语气和兴趣，避免每次都从零开始。')}</section>
  <section class="ability-section panel"><div class="ability-section-head"><span class="section-number">02</span><div><span class="eyebrow">EXPRESSION</span><h2>怎么把话说出来</h2><p>让表达贴近当下，而不是固定的回复模板。</p></div><span class="level-tag">表达能力</span></div>${abilityRow('images','message','平台鱼表情包','由模型选择是否配图，选中后随最后一段发送。')}<div class="inline-fields"><label>回复分段<select aria-label="回复分段" data-setting="split"><option value="adaptive" ${state.settings.split==='adaptive'?'selected':''}>模型判断 · 1–4 条</option><option value="single" ${state.settings.split==='single'?'selected':''}>只发一条</option></select></label><label>分条间隔<select aria-label="分条间隔" data-setting="gap"><option value="800" ${state.settings.gap==='800'?'selected':''}>800 毫秒 + 打字模拟</option><option value="1000" ${state.settings.gap==='1000'?'selected':''}>1 秒 + 打字模拟</option></select></label></div></section>
  <section class="ability-section panel"><div class="ability-section-head"><span class="section-number">03</span><div><span class="eyebrow">INITIATIVE</span><h2>什么时候主动靠近</h2><p>关注与判断分开，群聊和私聊各自保留节奏。</p></div><span class="level-tag advanced">主动能力</span></div>${abilityRow('group','spark','群聊自主接话','仅在白名单群判断；有新消息，或待判断消息到期后再看。')}<div class="nested-setting"><label for="group-interval">群聊判断最短间隔</label><div><input id="group-interval" data-setting="groupInterval" type="number" min="1" max="1440" required value="${escape(state.settings.groupInterval)}"><span>分钟</span></div><p>自己参与聊天后，重新计算该群的间隔。其他群互不影响。</p></div>${abilityRow('owner','heart','主动找主人','有合适的话题时再联系，不把定时检查变成定时发送。')}<div class="inline-fields nested-fields"><label>检查间隔<span class="number-field"><input aria-label="主人联系检查间隔" data-setting="ownerInterval" type="number" min="1" max="1440" required value="${escape(state.settings.ownerInterval)}">分钟</span></label><label>主人空闲至少<span class="number-field"><input aria-label="主人空闲时间" data-setting="ownerIdle" type="number" min="1" max="1440" required value="${escape(state.settings.ownerIdle)}">分钟</span></label><label>每日主动发送<span class="number-field"><input aria-label="每日主人主动发送上限" data-setting="ownerLimit" type="number" min="0" max="999" required value="${escape(state.settings.ownerLimit)}">次</span></label></div>${abilityRow('quiet','moon','尊重安静时段','安静时段、未回复保护和用户主动暂停继续生效。')}</section></div>
  <aside class="ability-aside"><section class="relationship panel"><span class="eyebrow">能力如何配合</span><h3>一条自然的对话，<br>从理解开始。</h3><ol><li><span>01</span><div><b>知道在和谁说话</b><small>可信身份、人格与权限</small></div></li><li><span>02</span><div><b>理解现在发生了什么</b><small>近期消息、记忆与注意力</small></div></li><li><span>03</span><div><b>选择怎么回应</b><small>情绪、兴趣与主动判断</small></div></li><li><span>04</span><div><b>把话好好送到</b><small>文字、分段与平台表情包</small></div></li></ol><p>主动能力使用基础信息，但不改变主人身份或管理权限。</p></section><section class="soft-note"><span>✳</span><p>不是开关越多，越像一个人。<br>留一点安静，也是一种陪伴。</p></section></aside></div>`;
}
function largeChart() {
  const samples = {
    machine:{max:1200,ram:[1082,1104,1062,1115,1072,1030,996,920,928,898,900,886,904,872,882,858,866,849,861,838,850,842.6],swap:[220,224,230,228,240,236,258,296,303,310,325,331,332,342,346,356,360,369,374,379,381,384.2]},
    napcat:{max:240,ram:[147,138,152,141,155,130,112,101,98,112,105,97,95,108,99,96,104,98,95,102,96,94.8],swap:[104,112,108,115,117,122,135,147,150,151,155,158,160,165,169,171,173,174,176,178,179,180.5]},
    ai:{max:180,ram:[96,88,99,85,104,76,63,44,38,42,37,36,47,39,38,35,41,37,39,35,38,36.8],swap:[70,72,74,79,77,84,91,101,105,107,108,110,112,115,116,119,120,121,122,124,125,126.2]},
  };
  const series=samples[state.monitor];
  const path=values=>values.map((value,i)=>`${i?'L':'M'}${(i/(values.length-1)*700).toFixed(1)} ${(190-value/series.max*190).toFixed(1)}`).join(' ');
  const ram=path(series.ram),swap=path(series.swap);
  return `<div class="chart" aria-label="示例：近一小时${state.monitor==='machine'?'整机 RAM':state.monitor==='napcat'?'NapCat PSS':'共享 AI PSS'}与 Swap 趋势，单位 MiB"><div class="chart-y">${[series.max,series.max*2/3,series.max/3,0].map(n=>`<span>${Math.round(n).toLocaleString('en-US')}</span>`).join('')}</div><div class="chart-plot"><div class="chart-grid"><i></i><i></i><i></i><i></i></div><svg viewBox="0 0 700 190" preserveAspectRatio="none" aria-hidden="true"><defs><linearGradient id="ram-fill" x1="0" y1="0" x2="0" y2="1"><stop stop-color="currentColor" stop-opacity=".15"/><stop offset="1" stop-color="currentColor" stop-opacity="0"/></linearGradient></defs><path class="chart-area" d="${ram} V190 H0Z"/><path class="ram-line" d="${ram}"/><path class="swap-line" d="${swap}"/></svg><span class="chart-annotation">空闲页面回收</span><div class="chart-x"><span>14:00</span><span>14:15</span><span>14:30</span><span>14:45</span><span>15:00</span></div></div></div>`;
}
function resourcesPage() {
  const groups = [['平台 API','1','28.4','14.1','normal'],['PostgreSQL','1','42.7','21.3','normal'],['Agent','1','22.1','18.6','normal'],['NapCat / QQ','2','94.8','180.5','normal'],['共享 AstrBot','1','36.8','126.2','shared'],['独立 AstrBot','0','—','—','muted']];
  return `${title('RESOURCES & RUNTIME / 03','运行轻一点，陪伴久一点','先看整机真实 RAM，再看每个组件的分摊物理内存。',`<span class="sample-time">${icon('clock')}示例采样 · 15:00:00</span>`)}<section class="resource-summary panel"><div class="ram-summary"><div class="eyebrow">整机实际 RAM 已用</div><div class="ram-value">842.6 <span>MiB</span></div><div class="ram-track"><i></i></div><p>总量 1,775.3 MiB <span>可用 932.7 MiB</span></p></div><div class="resource-stat"><span>磁盘 Swap</span><b>384.2 <small>MiB</small></b><p>换出逻辑页面</p></div><div class="resource-stat"><span>zswap 压缩池</span><b>61.4 <small>MiB</small></b><p>已包含在整机 RAM 中</p></div><div class="resource-stat"><span>真正在线 QQ</span><b>2 <small>/ 3</small></b><p><i class="live-dot"></i>1 个离线，进程已清退</p></div></section><div class="resource-grid"><section class="trend-panel panel"><div class="section-mini-head"><div><span class="eyebrow">MEMORY OVER TIME</span><h2>内存的变化，看得见</h2></div><select aria-label="趋势时间范围"><option>最近 1 小时</option><option>最近 6 小时</option></select></div><div class="chart-toolbar"><div class="segmented" role="group" aria-label="趋势组件">${[['machine','整机'],['napcat','NapCat'],['ai','共享 AI']].map(([id,label])=>`<button aria-pressed="${state.monitor===id}" data-monitor="${id}">${label}</button>`).join('')}</div><span class="chart-unit">MiB · 示例趋势</span></div>${largeChart()}<div class="chart-legend"><span><i class="ram-key"></i>${state.monitor==='machine'?'实际 RAM':'物理驻留 PSS'}</span><span><i class="swap-key"></i>Swap 逻辑页面</span><span class="legend-note">分别观察，不相加</span></div></section><aside class="pressure-panel panel"><span class="eyebrow">SYSTEM HEALTH</span><h2>压力保持平稳</h2><div class="pressure-score"><span>12.8<small>%</small></span><b>CPU 使用率</b></div><div class="pressure-lines"><div><span>内存压力</span><b class="good-text">低 · 0.02%</b></div><div><span>zswap 状态</span><b>已启用</b></div><div><span>Guard 保护</span><b class="good-text">运行中</b></div></div><p>${icon('shield')}发生持续压力时，自动回退保护优先。</p></aside></div>
  <section class="components-panel panel"><div class="section-mini-head"><div><span class="eyebrow">WHERE MEMORY GOES</span><h2>资源用在了哪里</h2></div><button class="text-button" data-action="metrics">读懂这些指标 ${icon('help')}</button></div><div class="table-scroll"><table><thead><tr><th>资源分类</th><th>实际进程</th><th>PSS <small>物理驻留</small></th><th>Swap <small>逻辑页面</small></th><th>运行状态</th></tr></thead><tbody>${groups.map(([name,n,pss,swap,type])=>`<tr><td>${icon(type==='shared'?'spark':name.startsWith('NapCat')?'fish':'server')}<b>${name}</b>${type==='shared'?'<span class="table-tag">2 个在线成员</span>':''}</td><td>${n}</td><td>${pss}${pss!=='—'?'<small> MiB</small>':''}</td><td>${swap}${swap!=='—'?'<small> MiB</small>':''}</td><td>${type==='muted'?'<span class="muted">未运行</span>':'<span class="status online"><i></i>正常</span>'}</td></tr>`).join('')}</tbody></table></div><div class="table-foot">共享 AI 按真实进程统计一次。PSS、USS、Swap 含义不同，不能相加充当物理 RAM。</div><details class="advanced"><summary>高级指标与采集说明 ${icon('down')}</summary><div><p>本页为示例数据。正式采集应保留 PID、进程树与归属；采集不到时显示“未知”。</p><dl><div><dt>PSS</dt><dd>分摊共享页面后的物理驻留内存。</dd></div><div><dt>USS</dt><dd>进程独占的物理驻留内存。</dd></div><div><dt>Swap</dt><dd>换出的逻辑页面，不直接等于额外物理 RAM。</dd></div><div><dt>zswap</dt><dd>压缩池实际物理占用，已计入整机 RAM。</dd></div></dl></div></details></section>`;
}
function render() {
  const theme = themes[state.theme];
  document.documentElement.dataset.theme = state.theme;
  document.documentElement.classList.toggle('reduce-motion', !state.motion);
  document.title = `${theme.name} · FishBot 设计预览`;
  $$('.version-nav button').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.theme===state.theme)));
  app.innerHTML = `<div class="workspace page-${state.page}"><aside class="sidebar">${nav()}</aside><div class="work-area">${header()}<main id="main" tabindex="-1">${({robots:robotsPage,abilities:abilitiesPage,resources:resourcesPage})[state.page]()}</main><footer class="workspace-footer"><span>FISHBOT <i>让陪伴，自然发生。</i></span><span>${theme.name} / ${theme.design}</span><span class="footer-issue">DESIGN STUDY ${theme.number} · 2026</span></footer></div></div>`;
}
function route() {
  const [theme,page] = location.hash.slice(1).split('/');
  const previousPage=state.page;
  state.theme = Object.hasOwn(themes,theme) ? theme : 'tidal';
  state.page = ['robots','abilities','resources'].includes(page) ? page : 'robots';
  render();
  clearTimeout(toastTimer);$('.toast').classList.remove('visible');
  if(previousPage!==state.page)$('#main').focus({preventScroll:true});
  window.scrollTo({top:0,behavior:'instant'});
}
function navigate(page) {location.hash = `${state.theme}/${page}`;}
function showDetail(id) {
  const bot = bots.find(b=>b.id===id); if (!bot) return;
  const dialog = $('#detail');
  dialog.innerHTML = `<div class="dialog-top"><span class="eyebrow">COMPANION DETAILS</span><button class="icon-button" data-action="close" aria-label="关闭详情">${icon('close')}</button></div><div class="dialog-identity">${avatar(bot)}<div><h2 id="detail-title">${bot.name}</h2><p>${bot.persona} · QQ ${bot.qq}</p></div></div>${status(bot)}<div class="detail-stats"><div><span>NapCat PSS</span><b>${bot.pss}<small>${bot.pss==='—'?'':' MiB'}</small></b></div><div><span>NapCat Swap</span><b>${bot.swap}<small>${bot.swap==='—'?'':' MiB'}</small></b></div></div><div class="detail-block"><span>关联 AI</span><b>${bot.state==='online'?'共享 AstrBot · 运行组 01':'按需恢复 · 当前未运行'}</b><p>${bot.state==='online'?'与另一位在线成员共享 1 个 AI 进程，身份和记忆独立。':'离线资源已释放，账号、QQ Session、配置与聊天记忆已保留。'}</p></div><div class="detail-block"><span>相处状态</span><b>${bot.mood} · 已保存记忆 ${bot.memory}</b><p>${bot.autonomy}</p></div><button class="button primary full" data-action="${bot.state==='online'?'configure':'restore'}">${bot.state==='online'?'调整能力与表达':'恢复登录流程预览'}${icon('arrow')}</button><p class="dialog-note">设计示例，不会操作实际机器人。</p>`;
  dialog.showModal();
}
document.addEventListener('click', event => {
  const button = event.target.closest('button'); if (!button) return;
  if (button.dataset.theme) {location.hash=`${button.dataset.theme}/${state.page}`; return;}
  if (button.dataset.page) {navigate(button.dataset.page);return;}
  if (button.dataset.open) {showDetail(button.dataset.open);return;}
  if (button.dataset.filter) {const filter=button.dataset.filter;state.filter=filter;render();$(`[data-filter="${filter}"]`).focus({preventScroll:true});return;}
  if (button.dataset.monitor) {const monitor=button.dataset.monitor;state.monitor=monitor;render();$(`[data-monitor="${monitor}"]`).focus({preventScroll:true});return;}
  if (button.dataset.toggle) {const key=button.dataset.toggle;state.flags[key]=!state.flags[key];button.classList.toggle('on',state.flags[key]);button.setAttribute('aria-checked',String(state.flags[key]));return;}
  switch(button.dataset.action) {
    case 'close': $('#detail').close();break;
    case 'configure': $('#detail').close();navigate('abilities');break;
    case 'restore': {
      const dialog=$('#detail');dialog.innerHTML=`<div class="dialog-top"><span class="eyebrow">WELCOME BACK</span><button class="icon-button" data-action="close" aria-label="关闭详情">${icon('close')}</button></div><h2 id="detail-title">让这条鱼，重新回来。</h2><ol class="recovery-steps"><li><span>1</span><div><b>先尝试已有 QQ 会话</b><p>能恢复时直接上线，不重复要求扫码。</p></div></li><li><span>2</span><div><b>恢复失败，再显示新二维码</b><p>区分已扫码、等待手机确认和登录受限。</p></div></li><li><span>3</span><div><b>连接成功后准备 AI</b><p>沿用原有的人格、模型配置和记忆。</p></div></li></ol><p class="dialog-note">登录流程展示，不会实际启动 QQ。</p>`;break;
    }
    case 'create': notify('预览：正式产品会进入创建流程，填写人格、模型并连接 QQ。');break;
    case 'save': if($$('main input[data-setting]').some(field=>!field.reportValidity()))break; notify('预览设置已保留在当前页面，未写入任何机器人配置。');break;
    case 'clear': state.search='';state.filter='all';render();break;
    case 'help': notify('先连接 QQ，再检查模型，最后按需要开启记忆与主动能力。');break;
    case 'metrics': $('.advanced').open=true;$('.advanced').scrollIntoView({behavior:state.motion?'smooth':'instant',block:'center'});break;
    case 'motion': state.motion=!state.motion;render();$('.motion-control').focus({preventScroll:true});notify(state.motion?'已开启轻动效':'已关闭轻动效');break;
  }
});
document.addEventListener('input', event=>{
  if(event.target.dataset.setting){const key=event.target.dataset.setting;if(Object.hasOwn(state.settings,key))state.settings[key]=event.target.value;return;}
  if(event.target.id==='search') {
    const position=event.target.selectionStart; state.search=event.target.value;
    $('.roster').innerHTML=botList()+`<div class="roster-foot">${icon('shield')}离线只释放运行资源，账号、配置和记忆继续保留。<span>随时回来，继续聊天。</span></div>`;
    $('#search').focus();$('#search').setSelectionRange(position,position);
  }
});
document.addEventListener('keydown',event=>{
  if(event.key==='/'&&!['INPUT','TEXTAREA','SELECT'].includes(document.activeElement.tagName)&&!$('#detail').open&&$('#search')) {event.preventDefault();$('#search').focus();}
});
$('#detail').addEventListener('click',event=>{if(event.target===$('#detail')){const rect=$('#detail').getBoundingClientRect();if(event.clientX<rect.left||event.clientX>rect.right||event.clientY<rect.top||event.clientY>rect.bottom)$('#detail').close();}});
window.addEventListener('hashchange',route);
route();
