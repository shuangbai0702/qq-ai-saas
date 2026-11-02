import {chromium} from 'playwright';
import assert from 'node:assert/strict';
import {randomUUID} from 'node:crypto';
import {mkdtemp,mkdir,writeFile} from 'node:fs/promises';
import path from 'node:path';
import {createApp} from '../apps/control-api/src/app.js';
import {issueTestInvite} from './helpers/invitations.js';

const current=await createApp({root:await mkdtemp(path.resolve('.local/proactive-ui-')),databaseUrl:'',agent:false});
const browser=await chromium.launch({headless:true}),previous=process.env.APP_ORIGIN,out='test-results/proactive';
await mkdir(out,{recursive:true});
try{
 const base=await current.app.listen({host:'127.0.0.1',port:0}),web=process.env.AUTONOMOUS_WEB_URL||'http://127.0.0.1:5173';process.env.APP_ORIGIN=web;
 const context=await browser.newContext({viewport:{width:1440,height:1000}});
 const baseConfigWrites:string[]=[];
 await context.route('**/v1/**',async route=>{const url=new URL(route.request().url());if(route.request().method()==='PUT'&&url.pathname.endsWith('/config'))baseConfigWrites.push(url.pathname);const response=await route.fetch({url:base+url.pathname+url.search});await route.fulfill({response})});
 assert.equal((await context.request.post(base+'/v1/auth/register',{data:{inviteCode:await issueTestInvite(current.db),email:'proactive@ui.test',password:'proactive-ui-test-123',name:'主动模式验收'}})).status(),200);
 const created=await context.request.post(base+'/v1/bots',{data:{name:'主动模式验收鱼'}});assert.equal(created.status(),201);const id=(await created.json()).id,url='/v1/bots/'+id+'/autonomous';
 await current.db.query("UPDATE bot_config_revisions SET config=jsonb_set(config,'{behavior,groupParticipationGroups}','[\"12345\",\"67890\"]') WHERE bot_id=$1",[id]);
 const goalId=randomUUID(),tenant=(await current.db.query('SELECT tenant_id FROM bots WHERE id=$1',[id]))[0].tenant_id;
 await current.db.query("INSERT INTO autonomy_goals(id,bot_id,tenant_id,origin,status,title,reason) VALUES($1,$2,$3,'model','active','独立目标控制验收','isolated browser fixture')",[goalId,id,tenant]);
 const page=await context.newPage(),errors:string[]=[];page.on('pageerror',error=>errors.push(error.message));await page.clock.install();
 await page.goto(web+'/#/bots/'+id+'/config/proactive');
 await page.getByLabel('主人判断间隔（分钟）',{exact:true}).fill('31');
 await page.evaluate(()=>{(window as any).autonomousBaseSubmits=0;document.getElementById('bot-config')!.addEventListener('submit',()=>{(window as any).autonomousBaseSubmits++})});
 await page.getByRole('navigation',{name:'配置分区'}).waitFor();
 await page.locator('[aria-label="机器人管理"]').getByRole('link',{name:'主动模式',exact:true}).click();await page.waitForURL('**/autonomous');
 const panel=page.getByRole('region',{name:'主动型 AI 生命周期'}),mode=panel.getByLabel('主动行为模式');
 await mode.waitFor();await page.getByRole('button',{name:'保存自主配置',exact:true}).waitFor();
 await panel.getByText('能力设置请在对应功能页面调整；开启主动模式不会额外开启空间、签名或联系权限。',{exact:true}).waitFor();
 assert.equal(await panel.getByRole('checkbox',{name:/研究兴趣|主动联系主人|参与白名单群|自主更新|自主发布/}).count(),0,'outer must not expose a second feature permission UI');

 assert.equal(await mode.inputValue(),'passive');assert.equal(await panel.getByLabel('主动执行范围').inputValue(),'shadow');
 assert.equal(await panel.locator('[aria-label="自主运行概览"]').count(),1);assert.equal(await panel.getByText('QQ 状态未知或已过期',{exact:false}).count(),1,'unknown health was shown as confirmed offline');
 await mode.selectOption('active');
 const interval=panel.getByLabel('自主思考间隔（分钟）');assert.equal(await interval.inputValue(),'5');
 const common=panel.getByLabel('共用群参与白名单');assert.match(await common.innerText(),/12345、67890/);assert.equal(await panel.getByLabel('主动模式群白名单').count(),0,'a duplicate whitelist is editable');
 await panel.getByText('自主疲劳与行动倾向',{exact:true}).click();
 assert.equal(await panel.getByLabel('提供自主活动节奏信息').isChecked(),true);
 await panel.getByLabel('活动统计窗口（分钟）',{exact:true}).fill('45');
 await panel.getByLabel('休息多久负荷减半（分钟）',{exact:true}).fill('25');
 await panel.getByLabel('活动负荷尺度（分钟）',{exact:true}).fill('15');
 await panel.getByLabel('空闲多久积累一半行动倾向（分钟）',{exact:true}).fill('20');
 await interval.fill('11');await interval.press('Enter');
 await panel.getByRole('button',{name:'保存自主配置',exact:true}).click();await panel.getByText('已保存，等待运行实例确认。QQ 连接保持运行。',{exact:true}).waitFor();
 const stored=await(await context.request.get(base+url)).json();assert.equal(stored.settings.config.minSleepSeconds,660);assert.equal(stored.settings.config.fatigueEnabled,true);assert.equal(stored.settings.config.fatigueWindowMinutes,45);assert.equal(stored.settings.config.fatigueRecoveryMinutes,25);assert.equal(stored.settings.config.fatigueCapacityMinutes,15);assert.equal(stored.settings.config.initiativeAfterIdleMinutes,20);assert.equal('groupAllowlist' in stored.settings.config,false);assert.equal(stored.settings.config.mode,'active');assert.equal(stored.settings.config.rollout,'shadow');assert.equal(stored.online,false);
 assert.deepEqual(baseConfigWrites,[],'autonomous save also submitted the surrounding base configuration form');
 const goalCard=panel.locator('article').filter({hasText:'独立目标控制验收'});
 await goalCard.getByRole('button',{name:'暂停目标',exact:true}).click();await goalCard.getByRole('button',{name:'恢复目标',exact:true}).waitFor();
 await goalCard.getByRole('button',{name:'恢复目标',exact:true}).click();await goalCard.getByRole('button',{name:'暂停目标',exact:true}).waitFor();
 await goalCard.getByRole('button',{name:'放弃目标',exact:true}).click();await goalCard.getByText('已放弃',{exact:false}).waitFor();
 assert.equal((await current.db.query('SELECT status FROM autonomy_goals WHERE bot_id=$1 AND id=$2',[id,goalId]))[0].status,'abandoned');
 // Hold a real old GET while a newer mode mutation and its own GET complete.
 let releaseOld!:()=>void,started!:()=>void;const held=new Promise<void>(resolve=>releaseOld=resolve),began=new Promise<void>(resolve=>started=resolve);let hold=true;
 await page.route('**'+url,async route=>{
  if(route.request().method()==='GET'&&hold){hold=false;const response=await route.fetch({url:base+url});started();await held;await route.fulfill({response});return}
  await route.fallback();
 });
 await interval.fill('12');assert.equal(await panel.getByRole('button',{name:'唤醒并重新判断',exact:true}).isDisabled(),true,'wake could discard an unsaved draft');
 const leaving=page.waitForEvent('dialog');const navigation=page.getByRole('link',{name:'QQ连接',exact:true}).click();const leaveDialog=await leaving;assert.match(leaveDialog.message(),/未保存/);await leaveDialog.dismiss();await navigation;
 await page.waitForURL('**/autonomous');assert.equal(await interval.inputValue(),'12','canceled same-bot navigation lost the autonomous draft');
 await page.clock.fastForward(10000);await began;
 await panel.getByRole('button',{name:'暂停主动模式',exact:true}).click();await page.waitForResponse(response=>response.url().endsWith(url)&&response.request().method()==='GET'&&response.status()===200);
 releaseOld();await page.waitForTimeout(100);assert.equal(await mode.inputValue(),'passive','late poll reverted the saved mode');
 assert.equal(await interval.inputValue(),'12','pausing discarded unrelated draft edits');const paused=await(await context.request.get(base+url)).json();assert.equal(paused.settings.config.mode,'passive');assert.equal(paused.settings.config.minSleepSeconds,660,'pausing silently saved the draft');
 await panel.getByRole('button',{name:'撤销未保存编辑',exact:true}).click();assert.equal(await interval.inputValue(),'11');
 assert.equal(await page.evaluate(()=>(window as any).autonomousBaseSubmits),0,'an autonomous control submitted the surrounding base form');
 assert.equal((await current.db.query('SELECT * FROM commands WHERE bot_id=$1',[id])).length,0,'autonomous controls started a QQ');
 await page.unroute('**'+url);await page.screenshot({path:out+'/desktop.png',fullPage:true,animations:'disabled'});
 await page.setViewportSize({width:390,height:844});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);await page.screenshot({path:out+'/mobile.png',fullPage:true,animations:'disabled'});
 await interval.fill('13');const confirmed=page.waitForEvent('dialog');const acceptedNavigation=page.getByRole('link',{name:'QQ连接',exact:true}).click();await(await confirmed).accept();await acceptedNavigation;await page.waitForURL('**/qq');
 await page.getByRole('link',{name:'基本配置',exact:true}).click();await page.getByRole('link',{name:'主动聊天',exact:true}).click();assert.equal(await page.getByLabel('主人判断间隔（分钟）',{exact:true}).inputValue(),'31','autonomous navigation lost the independent base draft');await page.locator('[aria-label="机器人管理"]').getByRole('link',{name:'主动模式',exact:true}).click();await page.getByRole('region',{name:'主动型 AI 生命周期'}).getByLabel('自主思考间隔（分钟）').click({trial:true});
 assert.equal(await page.getByLabel('自主思考间隔（分钟）').inputValue(),'11','confirmed discard unexpectedly saved the draft');
 assert.deepEqual(baseConfigWrites,[],'an autonomous control also submitted the surrounding base configuration form');
 const finalDetail=await(await context.request.get(base+'/v1/bots/'+id)).json();assert.equal(finalDetail.bot.revision,1);assert.equal(finalDetail.config.behavior.ownerContactIntervalMinutes,30,'autonomous controls saved the independent base draft');
 assert.deepEqual(errors,[]);await writeFile(out+'/result.json',JSON.stringify({passed:true,checks:['passive/shadow defaults','shared saved group whitelist and 10-minute default','real API persistence','late GET cannot revert mode','draft navigation cancel and explicit discard','Enter and autonomous buttons preserve the base draft','goal pause resume abandon','offline controls do not start QQ','desktop/mobile','no JS errors'],productionMutations:0},null,2));
 console.log('PASS current frontend proactive UI, durable controls, unknown health, draft-preserving pause and stale polling barrier');
}finally{await browser.close();await current.app.close();if(previous===undefined)delete process.env.APP_ORIGIN;else process.env.APP_ORIGIN=previous}
