import {chromium,firefox,webkit,request,type Browser} from 'playwright';
import assert from 'node:assert/strict';
import {mkdtemp,mkdir,writeFile} from 'node:fs/promises';
import path from 'node:path';
import {createApp} from '../apps/control-api/src/app.js';
import {issueTestInvite} from './helpers/invitations.js';
const baseline=process.env.WEB_LOADING_BASELINE==='1',out='.local/web-loading-20261125';await mkdir(out,{recursive:true});
const previous=process.env.APP_ORIGIN,results:any[]=[];
let active:Browser|undefined,lastPage:any,current:Awaited<ReturnType<typeof createApp>>|undefined;
try{
 const variants=baseline?[['chromium',chromium,undefined]]:[['chromium',chromium,undefined],['chrome',chromium,'chrome'],['edge',chromium,'msedge'],['firefox',firefox,undefined],['webkit',webkit,undefined]];
 for(const [name,engine,channel]of variants as any){
  // Each browser gets its own real API and rate-limit scope.
  current=await createApp({root:await mkdtemp(path.resolve('.local/web-loading-fixture-')),databaseUrl:'',agent:false});
 const base=await current.app.listen({host:'127.0.0.1',port:0});process.env.APP_ORIGIN=base;
 const seed=await request.newContext({baseURL:base});const registered=await seed.post('/v1/auth/register',{data:{inviteCode:await issueTestInvite(current.db,2),email:'web-loading@fixture.test',name:'加载测试',password:'loading-fixture-123'}});assert.equal(registered.status(),200);await current.db.query('UPDATE users SET is_admin=true WHERE id=$1',[(await registered.json()).user.id]);
 const bot=await seed.post('/v1/bots',{data:{name:'加载验收机器人',protocol:'mock'}});assert.equal(bot.status(),201);const id=(await bot.json()).id,state=await seed.storageState();await seed.dispose();
  active=await engine.launch({headless:true,...(channel?{channel}:{})});const context=await active!.newContext({viewport:{width:1440,height:1000},reducedMotion:'reduce',storageState:state});
  const page=await context.newPage();lastPage=page;const errors:string[]=[],requests:string[]=[];page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>requests.push(new URL(r.url()).pathname));
  const start=Date.now();await page.goto(base+'/#/bots');await page.getByRole('heading',{name:/^我的机器人/}).waitFor();const listMs=Date.now()-start;
  for(let i=0;i<3;i++){const ack=page.getByRole('button',{name:'知道了',exact:true}),later=page.getByRole('button',{name:'稍后再说',exact:true});if(await ack.isVisible())await ack.click();else if(await later.isVisible())await later.click();else break}
  const listRequests=[...requests];if(!baseline){assert.ok(!listRequests.some(x=>/\/assets\/(?:bot-detail|admin(?:-|\.)|character-card|create-bot|bilibili|console-statistics)/.test(x)),'list loads only list code');assert.ok(!listRequests.some(x=>/\/v1\/(?:admin|character-card)/.test(x)),'list must not fetch unused detail data')}
  if(baseline){results.push({name,version:active!.version(),listMs,listRequests});await page.screenshot({path:out+'/before-list.png',fullPage:true});await active!.close();active=undefined;await current.app.close();current=undefined;continue}
  await page.evaluate(hash=>{location.hash=hash},'/bots/'+id+'/config/profile');await page.getByLabel(/Persona/).waitFor();
  const original=await page.getByLabel(/Persona/).inputValue();await page.getByLabel(/Persona/).fill('页面加载草稿，请勿丢弃');
  const links=page.locator('.config-nav a');const model=links.filter({hasText:'模型连接'});await model.click();await page.getByRole('heading',{name:/模型(?:连接|设置)/}).waitFor();
  await links.filter({hasText:'人格与主人'}).click();assert.equal(await page.getByLabel(/Persona/).inputValue(),'页面加载草稿，请勿丢弃');page.once('dialog',d=>d.accept());await page.getByRole('button',{name:'放弃修改',exact:true}).click();assert.equal(await page.getByLabel(/Persona/).inputValue(),original);
  if(name==='chromium'){
   await page.evaluate(()=>{location.hash='/bots/new'});await page.getByRole('heading',{name:'创建机器人',exact:true}).waitFor();await page.getByRole('button',{name:'下一步',exact:true}).click();await page.getByRole('heading',{name:'模型与 QQ 协议',exact:true}).waitFor();
   await page.getByRole('button',{name:'上一步',exact:true}).click();await page.getByRole('heading',{name:'选择人格模板',exact:true}).waitFor();await page.evaluate(hash=>{location.hash=hash},'/bots/'+id+'/config/profile');await page.getByLabel(/Persona/).waitFor();
  }
  await page.getByRole('link',{name:'角色卡',exact:true}).click();await page.getByRole('tab',{name:'公共卡库',exact:true}).waitFor();await page.getByRole('tab',{name:/机器人装配/}).click();await page.getByRole('heading',{name:/角色卡 0\/5/}).waitFor();
  if(!baseline)assert.ok(requests.some(x=>/\/assets\/character-cards-/.test(x)),'card code is loaded on demand');
  await page.getByRole('link',{name:'QQ连接',exact:true}).click();await page.getByRole('heading',{name:'模拟 QQ 连接流程',exact:true}).waitFor();
  await page.getByRole('link',{name:'运行日志',exact:true}).click();await page.getByRole('heading',{name:'运行日志',exact:true}).waitFor();
  await page.getByRole('link',{name:'平台管理',exact:true}).click();await page.getByRole('heading',{name:'运营管理',exact:true}).waitFor();assert.ok(!requests.includes('/v1/admin/memory'));
  await page.getByRole('link',{name:'内存与进程',exact:true}).click();await page.getByRole('heading',{name:'整机概览',exact:true}).waitFor();
  await page.setViewportSize({width:390,height:844});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'mobile admin overflow');
  await page.getByRole('link',{name:/^我的机器人/}).click();await page.getByRole('heading',{name:/^我的机器人/}).waitFor();assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'mobile list overflow');await page.screenshot({path:out+'/'+(baseline?'before-':'')+name+'-mobile.png',fullPage:true});
  await context.clearCookies();await page.reload();await page.getByRole('heading',{name:'登录账号',exact:true}).waitFor();assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'mobile login overflow');await page.getByRole('button',{name:'持激活码注册',exact:true}).click();await page.getByRole('heading',{name:'注册账号',exact:true}).waitFor();
  if(name==='chromium'){
   const guarded=await active!.newContext({storageState:state});const edit=await guarded.newPage();
   await edit.route('**/assets/character-cards-*.js',route=>route.abort('failed'));
   await edit.goto(base+'/#/bots/'+id+'/config/profile');await edit.getByLabel(/Persona/).waitFor();
   const acknowledge=edit.getByRole('button',{name:'知道了',exact:true});if(await acknowledge.isVisible())await acknowledge.click();
   const defer=edit.getByRole('button',{name:'稍后再说',exact:true});if(await defer.isVisible())await defer.click();
   await edit.getByLabel(/Persona/).fill('资源失败后仍需保留的草稿');await edit.getByRole('link',{name:'角色卡',exact:true}).click();await edit.getByRole('heading',{name:'页面资源加载失败',exact:true}).waitFor();
   await edit.getByRole('link',{name:'配置',exact:true}).click();assert.equal(await edit.getByLabel(/Persona/).inputValue(),'资源失败后仍需保留的草稿');await guarded.close();
   const slow=await active!.newContext();const delayed=await slow.newPage();const cold:string[]=[];delayed.on('request',r=>cold.push(new URL(r.url()).pathname));
   let finish!:()=>void;const gate=new Promise<void>(resolve=>{finish=resolve});
   await delayed.route('**/v1/auth/me',async route=>{await gate;await route.fulfill({status:401,contentType:'application/json',body:'{}'})});
   await delayed.goto(base+'/',{waitUntil:'domcontentloaded'});await delayed.getByRole('heading',{name:'正在连接工作区…',exact:true}).waitFor();await delayed.waitForTimeout(2200);
   assert.ok(!cold.some(x=>/\/assets\/(?:workspace|start)-/.test(x)),'auth wait must not download workspace or decoration');finish();await delayed.getByRole('heading',{name:'登录账号',exact:true}).waitFor();await slow.close();
  }
  assert.deepEqual(errors,[]);const result={name,version:active!.version(),listMs,listRequests,requests,errors};results.push(result);console.log('PASS',name,active!.version(),listMs,'ms');await active!.close();active=undefined;await current.app.close();current=undefined;
 }
}catch(error){if(lastPage){await writeFile(out+'/failure.txt',await lastPage.locator('body').innerText());await lastPage.screenshot({path:out+'/failure.png',fullPage:true})}throw error}finally{await active?.close();await current?.app.close();if(previous===undefined)delete process.env.APP_ORIGIN;else process.env.APP_ORIGIN=previous;await writeFile(out+'/'+(baseline?'baseline':'browser-matrix')+'.json',JSON.stringify(results,null,2))}

