import {chromium} from 'playwright';import assert from 'node:assert/strict';import {mkdtemp,mkdir,writeFile} from 'node:fs/promises';import path from 'node:path';
import {createApp} from '../apps/control-api/src/app.js';import {issueTestInvite} from './helpers/invitations.js';import type {LoginSnapshot} from '../packages/contracts/index.js';
const out='test-results/onboarding-20261031';await mkdir(out,{recursive:true});const current=await createApp({root:await mkdtemp(path.resolve('.local/onboarding-browser-')),agent:false}),{app,db,agent}=current;
const previous=process.env.APP_ORIGIN,base=await app.listen({host:'127.0.0.1',port:0});process.env.APP_ORIGIN=base;
let ai=false,protocol=false,starts=0,login:LoginSnapshot={state:'waiting_scan',qr:'controlled-login-fixture'};
agent.shared.start=async()=>{ai=true};agent.shared.probe=async()=>({alive:ai,ready:ai});agent.shared.stop=async()=>{ai=false};
agent.protocols.napcat.beginLogin=async ctx=>{starts++;protocol=true;assert.notEqual(ctx.loginIntent,'qr')};agent.protocols.napcat.stop=async()=>{protocol=false};
agent.protocols.napcat.getLoginState=async()=>login;agent.protocols.napcat.getHealth=async()=>({alive:protocol,ready:protocol,online:protocol&&login.state==='online',loginState:login.state});agent.protocols.napcat.queryIdentity=async()=>login.state==='online'?'90123456':null;
agent.host.beginInspection=async()=>{};agent.host.endInspection=()=>{};
const browser=await chromium.launch({headless:true}),page=await browser.newPage({viewport:{width:1440,height:1000}}),errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
try{
 const auth=await app.inject({method:'POST',url:'/v1/auth/register',payload:{inviteCode:await issueTestInvite(db,2),name:'上手验收',email:'onboarding-browser@example.test',password:'onboarding-test-123'}});assert.equal(auth.statusCode,200);
 await page.goto(base);await page.getByLabel('邮箱地址').fill('onboarding-browser@example.test');await page.getByLabel('密码',{exact:true}).fill('onboarding-test-123');await page.getByRole('button',{name:'登录工作区'}).click();await page.getByRole('button',{name:'创建第一个机器人'}).click();
 await page.getByLabel('机器人名称').fill('新用户测试');await page.getByRole('button',{name:'下一步',exact:true}).click();await page.getByRole('button',{name:'下一步',exact:true}).click();await page.getByRole('button',{name:'创建并设置QQ',exact:true}).click();await page.getByRole('heading',{name:'连接你的 QQ',exact:true}).waitFor();
 await page.getByRole('button',{name:'启动并连接QQ',exact:true}).waitFor();assert.equal((await db.query('SELECT * FROM commands')).length,0,'configuration creation must not silently start runtimes');await page.getByText('可以先连接 QQ；AI 回复还需要模型 Key。',{exact:false}).waitFor();
 await page.screenshot({path:out+'/desktop-setup.png',fullPage:true});
 const id=(await db.query('SELECT id FROM bots'))[0].id;
 await page.goto(base+'/#/bots/'+id+'/config/profile');await page.getByRole('heading',{name:'接下来做什么'}).waitFor();
 await page.getByRole('link',{name:'我的机器人',exact:true}).click();await page.getByRole('button',{name:'启动新用户测试',exact:true}).click();await page.waitForURL('**/#/bots/'+id+'/qq');
 await agent.tick();await page.getByRole('img',{name:'QQ登录二维码',exact:true}).waitFor({timeout:15000});assert.equal(starts,1);
 login={state:'waiting_confirm'};await agent.tick();await page.getByText(/服务器已收到扫码回执/).waitFor({timeout:15000});assert.equal(await page.getByRole('img',{name:'QQ登录二维码',exact:true}).count(),0);
 login={state:'online',uin:'90123456'};agent.lastHealth=0;await agent.tick();await page.getByRole('heading',{name:'QQ 已连接',exact:true}).waitFor({timeout:15000});assert.equal(await page.getByRole('button',{name:'刷新 / 重新登录',exact:true}).count(),0,'online QQ must not expose accidental refresh');
 assert.equal((await page.request.post(base+`/v1/bots/${id}/login-sessions`,{data:{refresh:true}})).status(),409);assert.equal(starts,1);
 await page.setViewportSize({width:390,height:844});await page.screenshot({path:out+'/mobile-connected.png',fullPage:true});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
 assert.deepEqual(errors,[]);await writeFile(out+'/results.json',JSON.stringify({passed:true,checks:['login','create without hidden runtime startup','three next steps and missing Key guidance','list start routes to QR','same process QR','scanned state','online refresh hidden and API rejects','390px viewport no overflow','no JS errors']}));console.log('PASS onboarding browser with real API/database + controlled protocol receipts; no QQ or model I/O');
}finally{await browser.close();await app.close();if(previous===undefined)delete process.env.APP_ORIGIN;else process.env.APP_ORIGIN=previous}
