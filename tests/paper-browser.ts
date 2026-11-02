import {chromium,type Page} from 'playwright';
import assert from 'node:assert/strict';
import {mkdir,mkdtemp,writeFile} from 'node:fs/promises';
import path from 'node:path';
import {createApp} from '../apps/control-api/src/app.js';
import {issueTestInvite} from './helpers/invitations.js';

// Real application and real API, with an isolated database and no Agent startup.
// Request routing is scoped to this browser context; the user's Vite/API stay live.
const out=(process.env.PAPER_QA_OUT||'test-results/paper-integration-20261102')+'/';await mkdir(out,{recursive:true});
const current=await createApp({root:await mkdtemp(path.resolve('.local/paper-ui-')),databaseUrl:'',agent:false});
const browser=await chromium.launch({headless:true}),checks:any[]=[],errors:string[]=[],calls:{method:string;url:string}[]=[];
const check=(name:string,pass:boolean,detail:any={})=>{const result={name,passed:!!pass,...detail};checks.push(result);assert.ok(pass,JSON.stringify(result));console.log('PASS '+name);};
const email='paper-console@ui.test',password='paper-ui-isolated-20261102';
let cookie='';
let configGate:Promise<void>|undefined;
const request=async(method:any,url:string,payload?:any)=>current.app.inject({method,url,headers:{...(cookie?{cookie}:{}),...(payload?{'content-type':'application/json'}:{})},...(payload?{payload:JSON.stringify(payload)}:{})});
const instrument=()=>{
 const qa=(window as any).__paperQA={frames:0,pending:new Set<number>(),canvases:[] as HTMLCanvasElement[],gpu:new Map<string,Set<any>>(),gl:null};
 const raf=window.requestAnimationFrame.bind(window),caf=window.cancelAnimationFrame.bind(window),create=document.createElement.bind(document);
 window.requestAnimationFrame=callback=>{const id=raf(t=>{qa.pending.delete(id);qa.frames++;callback(t);});qa.pending.add(id);return id;};
 window.cancelAnimationFrame=id=>{qa.pending.delete(id);caf(id);};
 (document as any).createElement=function(tag:string,...args:any[]){const node=(create as any)(tag,...args);if(tag.toLowerCase()==='canvas')qa.canvases.push(node);return node;};
 const get=HTMLCanvasElement.prototype.getContext;
 (HTMLCanvasElement.prototype as any).getContext=function(type:string,...args:any[]){const value=(get as any).call(this,type,...args);if(type==='webgl2'&&value)qa.gl=value;return value;};
 for(const type of ['Shader','Program','Texture','Buffer']){
  const proto=WebGL2RenderingContext.prototype as any,make=proto['create'+type],remove=proto['delete'+type];qa.gpu.set(type,new Set());
  proto['create'+type]=function(...args:any[]){const value=make.apply(this,args);if(value)qa.gpu.get(type)!.add(value);return value;};
  proto['delete'+type]=function(value:any){qa.gpu.get(type)!.delete(value);return remove.call(this,value);};
 }
};
const coverage:any[]=[];
const settled=async(page:Page)=>{
 await page.waitForFunction(()=>document.body.dataset.paper==='ready');await page.waitForTimeout(140);
 // Check readiness and capture the same DOM snapshot atomically. A response
 // can otherwise mount new controls between the wait and a separate read.
 const snapshot=await page.waitForFunction(()=>{
  const controls=[...document.querySelectorAll('button,input[type=file],a.back,.setup-checklist a')].map(n=>{
  const r=n.getBoundingClientRect(),s=getComputedStyle(n),art=n.querySelector(':scope>.stock-art'),canvas=(n.matches('input[type=file]')?n.parentElement?.querySelector('.paper-file-plate canvas'):art?.querySelector('.pigment-layer')) as HTMLCanvasElement|undefined;
  return {tag:n.tagName,type:n.getAttribute('type'),text:(n.textContent||n.getAttribute('aria-label')||'').trim(),class:n.className,shown:r.width>0&&r.height>0&&s.visibility!=='hidden',disabled:n.matches(':disabled'),paper:!!art,canvas:!!canvas&&canvas.width>0&&canvas.height>0,rect:r.toJSON(),clipped:r.width>0&&n.clientWidth>0&&n.scrollWidth>n.clientWidth+2};
  });
  return controls.every(c=>!c.shown||c.canvas)?{route:location.hash,width:innerWidth,controls}:false;
 });
 coverage.push(await snapshot.jsonValue());await snapshot.dispose();
};
const paintPixels=async(page:Page,selector:string)=>page.locator(selector).evaluate((c:HTMLCanvasElement)=>{const d=c.getContext('2d')!.getImageData(0,0,c.width,c.height).data;let n=0;for(let i=3;i<d.length;i+=4)if(d[i]>3)n++;return n;});
try{
 const invite=await issueTestInvite(current.db,4),registered=await request('POST','/v1/auth/register',{email,password,name:'纸白工作台',inviteCode:invite});assert.equal(registered.statusCode,200);
 cookie=String(registered.headers['set-cookie']).split(';')[0];const user=registered.json().user;
 await current.db.query('UPDATE users SET is_admin=TRUE WHERE id=$1',[user.id]);
 const seeded=await request('POST','/v1/bots',{name:'大肥鱼 · 纸白工作台',protocol:'mock',runtimeMode:'shared'});assert.equal(seeded.statusCode,201);const id=seeded.json().id;
 const second=await request('POST','/v1/bots',{name:'备用小鱼',protocol:'mock',runtimeMode:'dedicated'});assert.equal(second.statusCode,201);
 const context=await browser.newContext({viewport:{width:1440,height:1000}});await context.addInitScript(instrument);
 await context.route('**/v1/**',async route=>{
  const req=route.request(),url=new URL(req.url());calls.push({method:req.method(),url:url.pathname});
  if(req.method()==='PUT'&&url.pathname.endsWith('/config')&&configGate)await configGate;
  const headers={...req.headers(),host:'127.0.0.1:5173'};delete (headers as any)['content-length'];
  const response=await current.app.inject({method:req.method() as any,url:url.pathname+url.search,headers,payload:req.postData()||undefined});
  const responseHeaders=Object.fromEntries(Object.entries(response.headers).filter(([,v])=>v!==undefined).map(([k,v])=>[k,Array.isArray(v)?v.join('\n'):String(v)]));
  await route.fulfill({status:response.statusCode,headers:responseHeaders,body:response.rawPayload});
 });
 const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
 await page.goto('http://127.0.0.1:5173/',{waitUntil:'networkidle'});await page.getByRole('heading',{name:'登录账号'}).waitFor();await settled(page);
 check('Login uses the original paper surfaces and local wallpaper',await page.locator('.auth .stock-art').count()===4&&await page.locator('body').getAttribute('data-paper-renderer')==='webgl2');
 await page.getByLabel('邮箱地址').fill(email);await page.getByLabel('密码',{exact:true}).fill('incorrect-password');await page.getByRole('button',{name:'登录工作区'}).click();await page.locator('.auth .alert').waitFor();await settled(page);
 check('Real login error remains readable inside the original paper form',!!await page.locator('.auth .alert').innerText()&&await page.locator('.auth .stock-art').count()===4);
 await page.getByLabel('密码',{exact:true}).fill(password);await page.getByRole('button',{name:'登录工作区'}).click();await page.getByRole('heading',{name:/我的机器人/}).waitFor();await settled(page);
 check('Real login reaches the existing robot list',await page.getByRole('heading',{name:'大肥鱼 · 纸白工作台',exact:true}).count()===1&&calls.filter(c=>c.url==='/v1/auth/login').length===2);
 await page.screenshot({path:out+'dashboard-desktop.png',fullPage:true});
 const baseline=await page.locator('.stock-art').count();await page.getByRole('textbox',{name:'搜索机器人'}).fill('备用');await settled(page);
 check('React list filtering keeps real actions and decoration consistent',await page.getByRole('heading',{name:'备用小鱼',exact:true}).count()===1&&await page.getByRole('heading',{name:'大肥鱼 · 纸白工作台',exact:true}).count()===0);
 await page.getByRole('textbox',{name:'搜索机器人'}).fill('');await settled(page);
 check('Repeated list replacement does not duplicate paper layers',baseline===await page.locator('.stock-art').count()&&await page.locator('.paper-surface').evaluateAll(nodes=>nodes.every(n=>n.querySelectorAll(':scope>.stock-art').length===1)));
 await page.getByRole('link',{name:'创建机器人',exact:true}).first().click();await page.getByRole('heading',{name:'选择人格模板'}).waitFor();await settled(page);await page.screenshot({path:out+'wizard-persona.png',fullPage:true});
 await page.locator('.persona').nth(1).click();await settled(page);
 check('Real React selected-state updates preserve the complete paper material',await page.locator('.persona.selected b').innerText()==='温和助手'&&await page.locator('.persona').evaluateAll(ns=>ns.every(n=>n.classList.contains('paper-action')&&n.classList.contains('stock-ready')&&n.querySelectorAll(':scope>.stock-art').length===1)));
 await page.locator('.persona').first().click();await settled(page);
 await page.getByRole('button',{name:'下一步',exact:true}).click();await page.getByLabel('模型 ID').waitFor();await settled(page);
 check('Existing creation wizard inputs remain usable under paper UI',await page.getByLabel('模型 ID').inputValue()==='deepseek-chat');
 await page.screenshot({path:out+'wizard-model.png',fullPage:true});
 await page.getByRole('link',{name:'我的机器人',exact:true}).click();await page.getByRole('heading',{name:'大肥鱼 · 纸白工作台',exact:true}).click();await page.getByLabel('System Prompt').waitFor();await settled(page);
 const prompt='纸面接入验收：保留原有角色、配置与业务行为。';await page.getByLabel('System Prompt').fill(prompt);
 const before=calls.filter(c=>c.method==='PUT'&&c.url==='/v1/bots/'+id+'/config').length;
 await page.getByRole('button',{name:'保存并应用',exact:true}).click();await page.getByText(/版本 2 已保存/).waitFor();await settled(page);
 check('Paper click does not duplicate the real config submission',calls.filter(c=>c.method==='PUT'&&c.url==='/v1/bots/'+id+'/config').length===before+1);
 check('Saved real API configuration is unchanged by the decoration',(await request('GET','/v1/bots/'+id)).json().config.systemPrompt===prompt);
 check('Heading and status text cannot inherit the previous transparent title effect',await page.locator('.detail-title,.detail-title .badge').evaluateAll(nodes=>nodes.every(n=>getComputedStyle(n).webkitTextFillColor!=='rgba(0, 0, 0, 0)')));
 await page.screenshot({path:out+'config-profile.png',fullPage:true});
 await page.getByRole('link',{name:'聊天与记忆',exact:true}).click();const toggle=page.getByRole('checkbox',{name:/自动记住明确/});await toggle.waitFor();await settled(page);
 const checked=await toggle.isChecked();await toggle.click();await page.waitForTimeout(170);
 check('Native paper switch updates the original React state exactly once',await toggle.isChecked()!==checked);
 const ink=await toggle.evaluate(input=>{const c=input.parentElement!.querySelector('.paper-native-plate .pigment-layer') as HTMLCanvasElement;const d=c.getContext('2d')!.getImageData(0,0,c.width,c.height).data;return Array.from(d).filter((_,i)=>i%4===3&&d[i]>3).length;});
 check('Native switch uses the copied paper pigment painter',ink>150,{ink});await page.screenshot({path:out+'config-switch-colour.png'});
 await toggle.focus();await page.keyboard.press('Space');check('Keyboard toggles the original native input',await toggle.isChecked()===checked);await page.keyboard.press('Space');
 let releaseSave!:()=>void;configGate=new Promise<void>(resolve=>{releaseSave=resolve;});
 try{
  await page.getByRole('button',{name:'保存并应用',exact:true}).click();await page.waitForFunction(()=>document.querySelector('#bot-config fieldset')?.matches(':disabled'));await settled(page);
  check('Saving preserves inherited fieldset disabled state on paper switches',await toggle.isDisabled()&&await toggle.evaluate(n=>n.parentElement!.querySelector('.paper-native-plate')?.getAttribute('data-disabled')==='true'));
  const pendingValue=await toggle.isChecked();await toggle.evaluate((n:HTMLInputElement)=>n.click());check('Disabled paper switch cannot change the real form value',await toggle.isChecked()===pendingValue);
 }finally{releaseSave();configGate=undefined;}
 await page.getByText(/版本 3 已保存/).waitFor();
 check('Native switch change persists through the real API',(await request('GET','/v1/bots/'+id)).json().config.behavior.memoryAutoCapture===!checked);
 for(const [section,heading]of [['capabilities','能力管理'],['faces','QQ内置表情'],['images','聊天表情包'],['gallery','普通图库'],['proactive','主动聊天'],['plugins','QQ扩展与插件']]){
  await page.evaluate(url=>{location.hash=url;},'/bots/'+id+'/config/'+section);await page.waitForTimeout(260);await settled(page);
  check('Actual config section renders: '+heading,await page.locator('.config-nav nav a.active').innerText()===heading&&await page.locator('.config-main .stock-art').count()>0);
  await page.setViewportSize({width:390,height:844});await settled(page);
  check('Actual config section fits mobile width: '+heading,await page.evaluate(()=>document.documentElement.scrollWidth===innerWidth));
  if(section==='capabilities')await page.locator('.notice').filter({has:page.getByRole('button',{name:'应用大肥鱼社交推荐',exact:true})}).screenshot({path:out+'recommendation-mobile.png'});
  await page.setViewportSize({width:1440,height:1000});await settled(page);
  if(['capabilities','images','proactive'].includes(section))await page.screenshot({path:out+'config-'+section+'.png',fullPage:true});
  if(section==='gallery'){
   await page.getByLabel('图片使用场景',{exact:true}).fill('纸面 UI 隔离上传');
   const png=await page.evaluate(()=>{const c=document.createElement('canvas');c.width=c.height=32;c.getContext('2d')!.fillRect(2,2,28,28);const data=c.toDataURL().split(',')[1];c.width=c.height=0;return data;});
   const file=page.getByLabel('上传表情PNG',{exact:true}),chooserEvent=page.waitForEvent('filechooser');await file.click();const chooser=await chooserEvent;await page.waitForTimeout(170);
   const fileInk=await file.evaluate(n=>{const c=n.parentElement!.querySelector('.paper-file-plate canvas') as HTMLCanvasElement,d=c.getContext('2d')!.getImageData(0,0,c.width,c.height).data;return Array.from(d).filter((v,i)=>i%4===3&&v>3).length;});
   check('Native file picker button receives the approved paper pigment',fileInk>150,{fileInk});
   await file.locator('..').screenshot({path:out+'native-file-feedback.png'});
   await chooser.setFiles({name:'paper-ui.png',mimeType:'image/png',buffer:Buffer.from(png,'base64')});
   const uploads=calls.filter(c=>c.method==='POST'&&c.url.endsWith('/gallery')).length;
   await page.getByRole('button',{name:'添加图库图片',exact:true}).click();await page.getByRole('button',{name:'编辑场景',exact:true}).waitFor();await settled(page);
   check('Native file upload reaches the existing API once',calls.filter(c=>c.method==='POST'&&c.url.endsWith('/gallery')).length===uploads+1);
   await page.getByRole('button',{name:'编辑场景',exact:true}).click();await page.getByRole('dialog').waitFor();await settled(page);
   check('Portal dialog receives the original paper panel and white buttons',await page.locator('dialog>.stock-art').count()===1&&await page.locator('dialog button>.stock-art').count()===2);
   await page.getByRole('dialog').getByLabel(/适合什么场景/).fill('隔离上传后的纸面编辑');
   const saves=calls.filter(c=>c.method==='PATCH'&&c.url.includes('/gallery/')).length;
   await page.screenshot({path:out+'edit-dialog.png'});await page.keyboard.press('Control+s');await page.getByRole('dialog').waitFor({state:'detached'});await settled(page);
   check('Dialog shortcut submits only its original form',calls.filter(c=>c.method==='PATCH'&&c.url.includes('/gallery/')).length===saves+1&&await page.getByText('隔离上传后的纸面编辑',{exact:true}).count()===1);
   const layerCount=await page.locator('.stock-art').count();
   for(let i=0;i<4;i++){await page.getByRole('button',{name:'编辑场景',exact:true}).click();await page.getByRole('dialog').waitFor();await settled(page);await page.keyboard.press('Escape');await page.getByRole('dialog').waitFor({state:'detached'});await settled(page);}
   check('Repeated dialog open/close releases its paper layers',await page.locator('.stock-art').count()===layerCount);
  }
 }
 await page.getByRole('link',{name:'QQ连接',exact:true}).click();await page.getByRole('heading',{name:'模拟 QQ 连接流程'}).waitFor();await settled(page);await page.screenshot({path:out+'qq-connection.png',fullPage:true});
 check('QQ screen keeps native login controls and the paper panels',await page.getByRole('button',{name:'获取登录二维码',exact:true}).count()===1&&await page.locator('.qq-page .stock-art').count()>0);
 await page.getByRole('link',{name:'运行日志',exact:true}).click();await page.locator('pre.logs').waitFor();await settled(page);await page.screenshot({path:out+'logs.png',fullPage:true});
 check('Log panel remains readable and data is loaded from the real API',calls.some(c=>c.url==='/v1/bots/'+id+'/logs')&&await page.locator('pre.logs').innerText()!=='正在读取运行日志…');
 await page.getByRole('link',{name:'平台管理',exact:true}).click();await page.locator('.admin-operations tbody tr').first().waitFor();await settled(page);await page.waitForTimeout(200);await page.screenshot({path:out+'admin-operations.png',fullPage:true});
 check('Administrator overview retains actual table controls',await page.locator('.admin-page-nav .active').innerText()==='用户与机器人'&&await page.locator('.admin-operations').count()>0);
 await page.getByRole('link',{name:'内存与进程',exact:true}).click();await settled(page);await page.waitForTimeout(200);await page.screenshot({path:out+'admin-memory.png',fullPage:true});
 check('Memory management uses the same paper language',await page.locator('.memory-monitor').count()===1&&await page.locator('.admin-page-nav .active').innerText()==='内存与进程');
 await page.getByRole('link',{name:'激活码管理',exact:true}).click();await settled(page);await page.waitForTimeout(180);await page.screenshot({path:out+'admin-invitations.png',fullPage:true});
 check('Invitation management retains its existing form',await page.locator('.admin-page-nav .active').innerText()==='激活码管理');
 await page.getByRole('link',{name:'我的机器人',exact:true}).click();await settled(page);
 for(const width of [320,390,560,768,1024,1440,1920]){
  await page.setViewportSize({width,height:1000});await page.waitForTimeout(120);const overflow=await page.evaluate(()=>document.documentElement.scrollWidth-innerWidth);
  check('Actual dashboard fits width '+width,overflow===0,{overflow});
 }
 await page.setViewportSize({width:390,height:844});await settled(page);await page.screenshot({path:out+'dashboard-mobile.png',fullPage:true});
 await page.evaluate(url=>{location.hash=url;},'/bots/'+id+'/config/chat');await page.getByRole('checkbox',{name:/自动记住明确/}).waitFor();await settled(page);await page.screenshot({path:out+'config-mobile.png',fullPage:true});await page.screenshot({path:out+'config-mobile-top.png'});
 const misaligned=await page.locator('.paper-surface').evaluateAll(nodes=>nodes.flatMap(n=>{const a=n.getBoundingClientRect(),b=n.querySelector(':scope>.stock-art')?.getBoundingClientRect();return a.width&&a.height&&b&&['left','top','width','height'].some(k=>Math.abs((a as any)[k]-(b as any)[k])>3)?[{classes:n.getAttribute('class'),node:a.toJSON(),surface:b.toJSON()}]:[];}));
 check('Every mobile paper layer stays anchored to its own element',misaligned.length===0,{misaligned});
 check('Mobile config and native switch plates stay inside viewport',await page.evaluate(()=>document.documentElement.scrollWidth===innerWidth));
 await page.setViewportSize({width:1440,height:1000});await page.evaluate(url=>{location.hash=url;},'/bots/'+id+'/config/profile');await settled(page);
 await page.getByLabel('System Prompt').fill('BFCache 保留中的未保存文本');
 await page.evaluate(()=>window.dispatchEvent(new PageTransitionEvent('pagehide',{persisted:true})));await page.waitForTimeout(80);const f=await page.evaluate(()=>(window as any).__paperQA.frames);await page.waitForTimeout(100);
 check('History-cache suspension stops procedural frames',f===await page.evaluate(()=>(window as any).__paperQA.frames));
 await page.evaluate(()=>window.dispatchEvent(new PageTransitionEvent('pageshow',{persisted:true})));await page.waitForTimeout(100);
 check('History-cache restoration preserves form drafts',await page.getByLabel('System Prompt').inputValue()==='BFCache 保留中的未保存文本'&&await page.locator('.stock-art').count()>0);
 await page.getByRole('button',{name:'暂停纸面动态',exact:true}).click();await page.waitForTimeout(100);const paused=await page.evaluate(()=>(window as any).__paperQA.frames);await page.waitForTimeout(150);
 check('The fixed motion button has an aligned paper face and pigment',await paintPixels(page,'.paper-motion .pigment-layer')>100&&await page.locator('.paper-motion').evaluate(n=>{const r=n.getBoundingClientRect();return getComputedStyle(n).position==='fixed'&&r.bottom<=innerHeight&&r.right<=innerWidth;}));
 check('Paper motion pause stops the shared clock',paused===await page.evaluate(()=>(window as any).__paperQA.frames)&&await page.evaluate(()=>(window as any).__paperQA.pending.size)===0);
 const buffers=await page.locator('canvas').evaluateAll(nodes=>nodes.map(n=>(n as HTMLCanvasElement).toDataURL()));await page.waitForTimeout(100);check('Paused procedural pixels stay fixed',JSON.stringify(buffers)===JSON.stringify(await page.locator('canvas').evaluateAll(nodes=>nodes.map(n=>(n as HTMLCanvasElement).toDataURL()))));
 await page.getByRole('button',{name:'开启纸面动态',exact:true}).click();await page.waitForTimeout(100);
 const gpu=await page.evaluate(()=>Object.fromEntries([...(window as any).__paperQA.gpu].map(([type,set]:any)=>[type,set.size])));
 check('Repeated page navigation retains only one shared material renderer',JSON.stringify(gpu)===JSON.stringify({Shader:2,Program:1,Texture:3,Buffer:1}),{gpu});
 check('No unintended runtime or QQ actions were sent',!calls.some(c=>c.method==='POST'&&(/\/actions$|\/login-sessions$/.test(c.url))));
 const missing=coverage.flatMap(s=>s.controls.filter((c:any)=>c.shown&&!c.canvas).map((c:any)=>({route:s.route,width:s.width,text:c.text,class:c.class}))),clipped=coverage.flatMap(s=>s.controls.filter((c:any)=>c.shown&&c.clipped).map((c:any)=>({route:s.route,width:s.width,text:c.text,class:c.class})));
 check('Every encountered button, file picker and shortcut has a live pigment layer',missing.length===0,{missing,snapshots:coverage.length});
 check('Encountered action labels remain inside their paper faces',clipped.length===0,{clipped});
 await page.evaluate(()=>window.dispatchEvent(new PageTransitionEvent('pagehide')));await page.waitForTimeout(100);
 const cleanup=await page.evaluate(()=>({pending:(window as any).__paperQA.pending.size,art:document.querySelectorAll('.stock-art,.paper-native-plate,.paper-star').length,gpu:[...(window as any).__paperQA.gpu.values()].every((s:any)=>s.size===0),live:(window as any).__paperQA.canvases.filter((c:HTMLCanvasElement)=>c.width&&c.height).length}));
 check('Exit releases generated layers, owned canvases and GPU objects',cleanup.pending===0&&cleanup.art===0&&cleanup.gpu&&cleanup.live===0,cleanup);
 await page.evaluate(()=>{window.dispatchEvent(new PageTransitionEvent('pagehide'));window.dispatchEvent(new Event('resize'));document.dispatchEvent(new PointerEvent('pointermove',{clientX:200,clientY:200}));});await page.waitForTimeout(80);
 check('Repeated exit and late input cannot restart the runtime',await page.evaluate(()=>(window as any).__paperQA.pending.size)===0&&await page.locator('.stock-art').count()===0);
 check('No browser runtime errors across actual pages',errors.length===0,{errors});
}finally{
 await writeFile(out+'verification.json',JSON.stringify({checks,errors,calls,productionMutations:0},null,2));await writeFile(out+'control-coverage.json',JSON.stringify(coverage,null,2));await browser.close();await current.app.close();
}
