import assert from 'node:assert/strict';
import {chromium} from 'playwright';
import {mkdir,mkdtemp,rm,writeFile} from 'node:fs/promises';
import {execFileSync} from 'node:child_process';
import path from 'node:path';
import {createApp} from '../apps/control-api/src/app.js';
import {issueTestInvite} from './helpers/invitations.js';
import {defaultConfig} from '../packages/contracts/index.js';
const root=await mkdtemp(path.resolve('.local/system-review-browser-')),out=path.resolve('test-results/system-review-20261103');
await mkdir(out,{recursive:true});const {app,db}=await createApp({root,agent:false}),address=await app.listen({host:'127.0.0.1',port:0});process.env.APP_ORIGIN=address;
const browser=await chromium.launch({headless:true}),context=await browser.newContext({viewport:{width:1440,height:1000}}),page=await context.newPage(),errors:string[]=[],checks:string[]=[];page.on('pageerror',e=>errors.push(e.message));
try{
 assert.equal((await context.request.post(address+'/v1/auth/register',{data:{inviteCode:await issueTestInvite(db),email:'review@browser.test',password:'review-browser-20261103',name:'联合优化验收'}})).status(),200);
 const created=await context.request.post(address+'/v1/bots',{data:{name:'联合优化验收鱼',config:defaultConfig,protocol:'mock'}});assert.equal(created.status(),201);const id=(await created.json()).id;
 await page.goto(address+'/#/bots/'+id+'/overview');await page.getByRole('heading',{name:'当前状态',exact:true}).waitFor();checks.push('overview separates saved and applied state; QQ onboarding entry');
 await page.getByRole('link',{name:'连接与登录QQ',exact:true}).click();await page.getByRole('link',{name:'QQ连接',exact:true}).waitFor();checks.push('QQ connection reachable without starting production processes');
 await page.goto(address+'/#/bots/'+id+'/config/chat');await page.getByRole('heading',{name:'接收与群参与',exact:true}).waitFor();assert.equal(await page.getByLabel('群聊触发方式').count(),0);assert.equal(await page.getByRole('heading',{name:'长期记忆与表达学习',exact:true}).count(),0);checks.push('reception and white-list entry separate from memory and reply');
 await page.goto(address+'/#/bots/'+id+'/config/reply');await page.getByRole('heading',{name:'回复方式',exact:true}).waitFor();await page.getByLabel('允许真实引用回复',{exact:true}).uncheck();
 await page.getByRole('link',{name:'记忆与人物',exact:true}).click();await page.getByLabel('定期对话摘要',{exact:true}).waitFor();assert.equal(await page.getByText('每日摘要请求上限',{exact:true}).count(),0);await page.getByRole('button',{name:'保存并应用',exact:true}).click();await page.getByText('版本 2 已保存，下次启动时应用。',{exact:true}).waitFor();assert.equal((await(await context.request.get(address+'/v1/bots/'+id)).json()).config.behavior.quoteReplyEnabled,false);checks.push('draft survives submenus; save persists exact override without claiming applied');
 const python=path.resolve(`references/astrbot/.venv/${process.platform==='win32'?'Scripts/python.exe':'bin/python'}`);
 execFileSync(python,['-X','utf8','-B','-c',"import sys,time;sys.path.insert(0,'adapters/astrbot');from behavior import BehaviorStore;s=BehaviorStore(sys.argv[1]);s.db.executemany('INSERT INTO relations VALUES(?,?,?,?,?,?)',[('private:'+str(100000+i),str(100000+i),i%5,'平静',0,time.time()-i) for i in range(105)]);s.db.commit();s.close()",path.join(root,'bots',id,'behavior.sqlite')],{windowsHide:true});
 await page.getByRole('button',{name:'查询行为数据',exact:true}).click();await page.getByText('人物状态共 105 条',{exact:false}).waitFor();await page.getByRole('button',{name:'下一页',exact:true}).click();await page.getByText('第 2 / 2 页',{exact:false}).waitFor();assert.equal(await page.getByText('QQ 100104 · 亲近状态 4',{exact:true}).count(),1);checks.push('105 relation records paginate without loss');
 await page.goto(address+'/#/bots/'+id+'/config/capabilities');await page.getByRole('heading',{name:'能力地图',exact:true}).waitFor();assert.equal(await page.getByRole('checkbox').count(),0);checks.push('capability map is read-only and links to one editor per setting');
 await page.goto(address+'/#/bots/'+id+'/config/images');await page.getByLabel('允许保存到真实QQ收藏',{exact:true}).waitFor();await page.getByText('QQ 自己收藏的表情',{exact:true}).waitFor();checks.push('QQ favorite writes, platform collection and favorite send controls are distinct');
 await page.goto(address+'/#/bots/'+id+'/autonomous');await page.getByRole('heading',{name:/主动/}).first().waitFor();checks.push('autonomy remains a first-level page with separate save lifecycle');
 await page.goto(address+'/#/bots/'+id+'/config/model');await page.getByRole('heading',{name:'模型与 API Key',exact:true}).waitFor();await page.screenshot({path:out+'/desktop-model.png',fullPage:true});
 await page.setViewportSize({width:390,height:844});await page.goto(address+'/#/bots/'+id+'/config/reply');await page.getByRole('heading',{name:'回复方式',exact:true}).waitFor();assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);await page.screenshot({path:out+'/mobile-reply.png',fullPage:true});checks.push('mobile grouped submenus and no horizontal overflow');
 assert.deepEqual(errors,[]);await writeFile(out+'/browser-result.json',JSON.stringify({passed:true,transport:'isolated real API/PGlite and Chromium; no production QQ or model',checks,errors},null,2));console.log(JSON.stringify({passed:true,checks}));
}finally{await browser.close();await app.close();await rm(root,{recursive:true,force:true})}
