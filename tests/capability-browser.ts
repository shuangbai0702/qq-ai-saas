// Real API + built frontend with isolated data. No production credentials/QQ.
import {chromium} from 'playwright';
import assert from 'node:assert/strict';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {mkdtemp,writeFile,mkdir} from 'node:fs/promises';
import path from 'node:path';
import {createApp} from '../apps/control-api/src/app.js';
import {issueTestInvite} from './helpers/invitations.js';
const current=await createApp({root:await mkdtemp(path.resolve('.local/capability-browser-')),agent:false});
const browser=await chromium.launch({headless:true}),previous=process.env.APP_ORIGIN;
const output=process.env.CAPABILITY_OUTPUT||'test-results/capability-management';await mkdir(output,{recursive:true});
try{
 const address=await current.app.listen({host:'127.0.0.1',port:0});process.env.APP_ORIGIN=address;
 const context=await browser.newContext({viewport:{width:1440,height:1000}}),errors:string[]=[];
 const registered=await context.request.post(address+'/v1/auth/register',{data:{inviteCode:await issueTestInvite(current.db),email:'browser@capability.test',password:'capability-browser-123',name:'能力管理验收'}});assert.equal(registered.status(),200);
 const created=await context.request.post(address+'/v1/bots',{data:{name:'能力管理验收'}});assert.equal(created.status(),201);const id=(await created.json()).id;
 await current.db.query("UPDATE bot_config_revisions SET config=jsonb_set(config,'{behavior,socialAwareness}','false') WHERE bot_id=$1",[id]);
 const python=path.resolve(`references/astrbot/.venv/${process.platform==='win32'?'Scripts/python.exe':'bin/python'}`);
 await promisify(execFile)(python,['tests/capability_data_test.py','--seed',path.join(current.root,'bots',id,'behavior.sqlite')],{windowsHide:true});
 const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));await page.goto(address+`/#/bots/${id}/config/capabilities`);
 await page.getByRole('heading',{name:'能力管理',exact:true}).waitFor();await page.getByText('饮料：红茶',{exact:true}).waitFor();
 await page.getByRole('checkbox',{name:'按语境接话与沉默',exact:true}).check();await page.getByRole('button',{name:'保存并应用',exact:true}).click();await page.getByText(/版本 2 已保存/).waitFor();await page.reload();await page.getByRole('checkbox',{name:'按语境接话与沉默',exact:true}).waitFor();assert.equal(await page.getByRole('checkbox',{name:'按语境接话与沉默',exact:true}).isChecked(),true);
 const article=page.locator('article').filter({hasText:'饮料：红茶'});
 // A rejected write must remain visible in the editor, without changing data.
 await page.route('**/v1/bots/'+id+'/behavior-data',async route=>{if(route.request().method()==='POST')await route.fulfill({status:409,contentType:'application/json',body:JSON.stringify({error:'记忆版本已变化，请重新查询'})});else await route.continue()});
 await article.getByRole('button',{name:'纠正',exact:true}).click();await page.getByRole('dialog').getByLabel('事实内容').fill('不能提交的测试修改');await page.getByRole('dialog').getByRole('button',{name:'保存修改',exact:true}).click();await page.getByRole('dialog').getByRole('alert').getByText('记忆版本已变化，请重新查询',{exact:true}).waitFor();
 await page.screenshot({path:output+'/edit-error-desktop.png',fullPage:true});await page.getByRole('dialog').getByRole('button',{name:'取消',exact:true}).click();await article.waitFor();await page.unroute('**/v1/bots/'+id+'/behavior-data');
 await article.getByRole('button',{name:'纠正',exact:true}).click();await page.getByRole('dialog').getByLabel('事实内容').fill('桂花乌龙');await page.getByRole('dialog').getByRole('button',{name:'保存修改',exact:true}).click();await page.getByText('饮料：桂花乌龙',{exact:true}).waitFor();
 await page.getByRole('button',{name:'停用表达',exact:true}).click();await page.getByText('有节目效果 · 已停用',{exact:true}).waitFor();
 await page.getByLabel('会话类型',{exact:true}).selectOption('group');await page.getByLabel('会话号码',{exact:true}).fill('12345');const queryDone=page.waitForResponse(r=>r.url().includes('/behavior-data?scope='));await page.getByRole('button',{name:'查询行为数据',exact:true}).click();await queryDone;await page.getByText('生日：五月一日',{exact:true}).waitFor({state:'detached'});
 await page.screenshot({path:output+'/capabilities-desktop.png',fullPage:true});
 page.once('dialog',d=>d.accept());await page.getByRole('button',{name:'清空该群学习材料',exact:true}).click();await page.getByText('尚无符合条件的群体表达；开启学习不代表已经学到内容。',{exact:true}).waitFor();
 const updated=page.locator('article').filter({hasText:'饮料：桂花乌龙'});page.once('dialog',d=>d.accept());await updated.getByRole('button',{name:'遗忘',exact:true}).click();await page.getByText('当前条件下没有长期事实。',{exact:true}).waitFor();
 await page.getByRole('link',{name:'聊天表情包',exact:true}).click();await page.getByLabel('图片使用场景',{exact:true}).fill('轻松开玩笑');await page.getByLabel('上传表情PNG').setInputFiles('templates/reactions/laugh.png');await page.getByRole('button',{name:'添加表情图片',exact:true}).click();await page.getByRole('img',{name:'轻松开玩笑',exact:true}).waitFor();
 const uploaded=page.getByRole('img',{name:'轻松开玩笑',exact:true}).locator('..');await uploaded.getByRole('button',{name:'停用图片',exact:true}).click();await page.getByRole('button',{name:'启用图片',exact:true}).waitFor();
 await uploaded.getByRole('button',{name:'编辑场景',exact:true}).click();await page.getByRole('dialog').getByLabel('适合什么场景').fill('群友互相开玩笑');await page.getByRole('dialog').getByLabel('哪些场景不要使用').fill('认真讨论时不用');await page.getByRole('dialog').getByRole('button',{name:'保存修改',exact:true}).click();await page.getByRole('img',{name:'群友互相开玩笑',exact:true}).waitFor();await page.getByText('避免：认真讨论时不用',{exact:true}).waitFor();
 await page.reload();await page.getByRole('button',{name:'启用图片',exact:true}).waitFor();await page.screenshot({path:output+'/gallery-desktop.png',fullPage:true});
 await page.getByRole('link',{name:'能力管理',exact:true}).click();await page.getByRole('heading',{name:'能力管理',exact:true}).waitFor();await page.getByRole('heading',{name:'长期事实',exact:true}).waitFor();await page.setViewportSize({width:390,height:844});await page.screenshot({path:output+'/capabilities-mobile.png',fullPage:true});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
 assert.deepEqual(errors,[]);await writeFile(output+'/browser.json',JSON.stringify({passed:true,checks:['real API','independent awareness toggle persists','fact edit/forget','pattern disable/clear','image disable/scene edit persists','mobile no overflow'],qqMessagesSent:0,errors},null,2));console.log('PASS capability browser');
}finally{await browser.close();await current.app.close();if(previous===undefined)delete process.env.APP_ORIGIN;else process.env.APP_ORIGIN=previous}
