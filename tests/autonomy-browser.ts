import {chromium} from 'playwright';
import assert from 'node:assert/strict';
import {mkdtemp,mkdir,writeFile} from 'node:fs/promises';
import path from 'node:path';
import {createApp} from '../apps/control-api/src/app.js';
import {issueTestInvite} from './helpers/invitations.js';

const current=await createApp({root:await mkdtemp(path.resolve('.local/autonomy-browser-')),agent:false}),browser=await chromium.launch({headless:true});
const previous=process.env.APP_ORIGIN,out='test-results/autonomy';await mkdir(out,{recursive:true});
try{
 const address=await current.app.listen({host:'127.0.0.1',port:0});process.env.APP_ORIGIN=address;
 const context=await browser.newContext({viewport:{width:1440,height:1000}}),errors:string[]=[];
 const auth=await context.request.post(address+'/v1/auth/register',{data:{inviteCode:await issueTestInvite(current.db),email:'autonomy-ui@test.invalid',password:'autonomy-ui-test-123',name:'自主行为界面'}});assert.equal(auth.status(),200);
 const created=await context.request.post(address+'/v1/bots',{data:{name:'自主行为界面'}});assert.equal(created.status(),201);const id=(await created.json()).id,page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
 await page.goto(address+'/#/bots/'+id+'/config/capabilities');await page.getByRole('heading',{name:'能力管理',exact:true}).waitFor();
 for(const name of ['基础：上下文与记忆','持续状态：人格与关系','对话表现：表达与图片','高级行动：自主参与与联系','QQ工具：明确权限与操作'])await page.getByRole('heading',{name,exact:true}).waitFor();
 await page.getByRole('checkbox',{name:'话题注意力',exact:true}).uncheck();assert.equal(await page.getByRole('checkbox',{name:'长期记忆语义召回',exact:true}).isDisabled(),false);assert.equal(await page.getByRole('checkbox',{name:'自然群友交流',exact:true}).isDisabled(),false);
 const save=async()=>{const done=page.waitForResponse(r=>r.url().endsWith(`/v1/bots/${id}/config`)&&r.request().method()==='PUT');await page.getByRole('button',{name:'保存并应用',exact:true}).click();assert.equal((await done).status(),200)};
 await save();await page.goto(address+'/#/bots/'+id+'/config/chat');await page.getByRole('heading',{name:'白名单群自主参与',exact:true}).waitFor();await page.getByText('尚未选择白名单群，自主入场不会执行；正常被叫回复和已开始的跟聊仍按设置运行。',{exact:true}).waitFor();
 await page.getByLabel('自主参与白名单群号',{exact:true}).fill('778899, 889900');await page.getByLabel('群判断最短间隔（分钟）',{exact:true}).fill('20');await page.getByLabel('参与风格',{exact:true}).selectOption('active');await save();await page.reload();await page.getByLabel('群判断最短间隔（分钟）',{exact:true}).waitFor();assert.equal(await page.getByLabel('群判断最短间隔（分钟）',{exact:true}).inputValue(),'20');assert.equal(await page.getByLabel('参与风格',{exact:true}).inputValue(),'active');
 await page.screenshot({path:out+'/group-desktop.png',fullPage:true,animations:'disabled'});
 await page.goto(address+'/#/bots/'+id+'/config/proactive');await page.getByRole('heading',{name:'定时判断是否找主人聊天',exact:true}).waitFor();await page.getByRole('checkbox',{name:'定时主动联系总开关',exact:true}).check();await page.getByLabel('主人 QQ',{exact:true}).fill('11111111');await page.getByLabel('主人判断间隔（分钟）',{exact:true}).fill('90');await save();
 const detail=await context.request.get(address+'/v1/bots/'+id),data=await detail.json();assert.deepEqual(data.config.behavior.groupParticipationGroups,['778899','889900']);assert.equal(data.config.behavior.groupParticipationIntervalMinutes,20);assert.equal(data.config.behavior.ownerContactIntervalMinutes,90);assert.equal(data.config.behavior.attentionEnabled,false);
 await page.setViewportSize({width:390,height:844});await page.screenshot({path:out+'/owner-mobile.png',fullPage:true,animations:'disabled'});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
 await page.goto(address+'/#/bots/'+id+'/config/capabilities');await page.getByRole('heading',{name:'高级行动：自主参与与联系',exact:true}).waitFor();await page.screenshot({path:out+'/capabilities-mobile.png',fullPage:true,animations:'disabled'});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);assert.deepEqual(errors,[]);
 await writeFile(out+'/browser.json',JSON.stringify({passed:true,checks:['five responsibility groups','attention is not a master dependency','empty whitelist explanation','group interval and style persist','owner settings persist','390px no overflow'],errors},null,2));console.log('PASS autonomy UI');
}finally{await browser.close();await current.app.close();if(previous===undefined)delete process.env.APP_ORIGIN;else process.env.APP_ORIGIN=previous}
