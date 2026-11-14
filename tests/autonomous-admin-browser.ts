import {chromium} from 'playwright';
import assert from 'node:assert/strict';
import {randomUUID} from 'node:crypto';
import {mkdir,mkdtemp,writeFile} from 'node:fs/promises';
import path from 'node:path';
import {createApp} from '../apps/control-api/src/app.js';
import {AutonomyRepository} from '../packages/database/autonomous.js';
import {issueTestInvite} from './helpers/invitations.js';

const current=await createApp({root:await mkdtemp(path.resolve('.local/autonomous-admin-ui-')),databaseUrl:'',agent:false});
const browser=await chromium.launch({headless:true}),out='test-results/autonomous-admin',previous=process.env.APP_ORIGIN,errors:string[]=[];
await mkdir(out,{recursive:true});
try{
 const base=await current.app.listen({host:'127.0.0.1',port:0}),web=process.env.AUTONOMOUS_WEB_URL||'http://127.0.0.1:5173';process.env.APP_ORIGIN=web;
 const admin=await browser.newContext({viewport:{width:1440,height:1000}}),member=await browser.newContext();
 const register=async(context:any,email:string)=>{const r=await context.request.post(base+'/v1/auth/register',{data:{inviteCode:await issueTestInvite(current.db,3),email,password:'isolated-ui-password-123',name:email}});assert.equal(r.status(),200);return(await r.json()).user};
 const a=await register(admin,'admin@autonomous-ui.test'),b=await register(member,'member@autonomous-ui.test');await current.db.query('UPDATE users SET is_admin=true WHERE id=$1',[a.id]);
 const create=async(name:string)=>{const r=await member.request.post(base+'/v1/bots',{data:{name,protocol:'mock'}});assert.equal(r.status(),201);return(await r.json()).id};
 const id=await create('待核对测试鱼'),peer=await create('保持主动的同租户鱼'),[bot]=await current.db.query('SELECT * FROM bots WHERE id=$1',[id]);
 const repo=new AutonomyRepository(current.db);for(const item of [id,peer]){await repo.configure(bot.tenant_id,item,1,{mode:'active'});await repo.applied(bot.tenant_id,item,2)}
 await current.db.query("UPDATE bots SET desired_state='running',health=$2 WHERE id=$1",[id,JSON.stringify({sampledAt:new Date(Date.now()-120000).toISOString(),protocol:{online:true,token:'PRIVATE-HEALTH-MARKER'}})]);
 await current.db.query("INSERT INTO autonomy_cycles(id,bot_id,tenant_id,epoch,generation,config_revision,owner,state,budget,reason,work_expires_at,finished_at) VALUES('ui-cycle',$1,$2,2,1,1,'fixture','ended','{}','lease_expired',now(),now())",[id,bot.tenant_id]);
 // These passive metadata fixtures have no pending configuration; the one
 // unknown action stays visible in the attention filter before pagination.
 for(let i=0;i<20;i++){const fixture=randomUUID();await current.db.query("INSERT INTO bots(id,tenant_id,name,protocol) VALUES($1,$2,$3,'mock')",[fixture,bot.tenant_id,'分页样本 '+i]);await repo.ensure(bot.tenant_id,fixture);await repo.applied(bot.tenant_id,fixture,1)}
 for(const context of [admin,member])await context.route('**/v1/**',async route=>{const url=new URL(route.request().url()),response=await route.fetch({url:base+url.pathname+url.search});await route.fulfill({response})});
 const page=await admin.newPage();page.on('pageerror',e=>errors.push(e.message));await page.goto(web+'/#/admin/autonomous');
 const panel=page.getByRole('region',{name:'自主任务运维'});await panel.getByRole('row').filter({hasText:'待核对测试鱼'}).waitFor();
 assert.equal(await panel.getByText('QQ 状态未知或已过期',{exact:true}).count(),1);assert.ok(!(await panel.innerText()).includes('PRIVATE-'));
 const row=panel.getByRole('row').filter({hasText:'待核对测试鱼'});await row.getByRole('button',{name:'查看回执',exact:true}).click();const receipts=page.getByRole('region',{name:'未确认动作回执'});await receipts.getByText('结果未确认 · research',{exact:true}).waitFor();await receipts.getByText('动作身份与回执',{exact:true}).click();await receipts.getByText('ui-cycle',{exact:true}).waitFor();assert.equal(await receipts.getByRole('button',{name:/重发|标记成功/}).count(),0);assert.ok(!(await receipts.innerText()).includes('PRIVATE-'));
 await page.screenshot({path:out+'/desktop-attention.png',fullPage:true,animations:'disabled'});
 await row.getByRole('button',{name:'暂停主动模式',exact:true}).click();const form=panel.getByRole('region',{name:'暂停单个机器人的主动模式'});assert.equal(await form.getByRole('button',{name:'确认暂停主动模式',exact:true}).isDisabled(),true);await form.getByLabel('暂停原因').fill('核对未确认的原始投递');await form.getByRole('button',{name:'确认暂停主动模式',exact:true}).click();await panel.getByText('已保存暂停操作和原因，等待运行实例确认；QQ 连接保持运行。',{exact:true}).waitFor();
 assert.equal((await repo.view(bot.tenant_id,id)).settings.config.mode,'passive');assert.equal((await repo.view(bot.tenant_id,peer)).settings.config.mode,'active');assert.equal((await repo.unconfirmed(bot.tenant_id,id))[0].id,'ui-cycle');assert.equal((await current.db.query("SELECT * FROM audit_events WHERE event='admin.autonomous.paused'")).length,1);
 assert.equal((await admin.request.get(base+'/v1/bots/'+id+'/autonomous')).status(),404,'admin UI broadened ordinary tenant ownership');
 await panel.getByLabel('自主任务筛选').selectOption('all');await panel.getByText('共 22 个匹配机器人',{exact:false}).waitFor();assert.equal(await panel.getByRole('row').count(),21);await panel.getByRole('button',{name:'下一页',exact:true}).click();await panel.getByText('第 2 / 2 页',{exact:false}).waitFor();assert.equal(await panel.getByRole('row').count(),3);
 await page.screenshot({path:out+'/desktop.png',fullPage:true,animations:'disabled'});await page.setViewportSize({width:390,height:844});await panel.getByLabel('自主任务筛选').selectOption('attention');await panel.getByRole('row').filter({hasText:'待核对测试鱼'}).waitFor();assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);await page.screenshot({path:out+'/mobile.png',fullPage:true,animations:'disabled'});
 const other=await member.newPage();other.on('pageerror',e=>errors.push(e.message));await other.goto(web+'/#/admin/autonomous');await other.getByRole('heading',{name:'无权访问'}).waitFor();assert.equal((await member.request.get(base+'/v1/admin/autonomous')).status(),403);
 assert.equal((await current.db.query('SELECT * FROM commands')).length,0,'UI controls started a protocol runtime');assert.deepEqual(errors,[]);
 await writeFile(out+'/result.json',JSON.stringify({passed:true,frontend:web,realIsolatedApi:true,productionMutations:0,checks:['admin route and paper layout','expired health stays unknown','bounded original receipts','private data excluded','reason required and audit saved','single bot pause preserves peer and unknown','tenant boundary','22-row pagination','mobile layout','no QQ commands or browser errors']},null,2));console.log('PASS current frontend admin autonomous operations with real isolated API, receipts, pause, isolation and pagination');
}finally{await browser.close();await current.app.close();if(previous===undefined)delete process.env.APP_ORIGIN;else process.env.APP_ORIGIN=previous}
