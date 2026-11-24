import {chromium} from 'playwright';
import assert from 'node:assert/strict';
import {mkdtemp,mkdir,writeFile} from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import {createApp} from '../apps/control-api/src/app.js';
import {issueTestInvite} from './helpers/invitations.js';
const root=await mkdtemp(path.join(os.tmpdir(),'llbot-web-')),{app,db}=await createApp({root,agent:false});
await app.listen({host:'127.0.0.1',port:0});const base=app.listeningOrigin,oldOrigin=process.env.APP_ORIGIN,output='test-results/llbot';await mkdir(output,{recursive:true});process.env.APP_ORIGIN=base;
const browser=await chromium.launch({headless:true}),context=await browser.newContext({viewport:{width:1440,height:1000}}),page=await context.newPage(),errors:string[]=[];page.on('pageerror',error=>errors.push(error.message));
try{
 const response=await context.request.post(base+'/v1/auth/register',{data:{email:'llbot-ui@example.test',password:'llbot-ui-test-123',name:'双协议验收',inviteCode:await issueTestInvite(db,2)}});assert.equal(response.status(),200);
 await page.goto(base);await page.getByRole('button',{name:'创建第一个机器人'}).waitFor();
 const dialog=page.locator('dialog.announcement-dialog[open]');if(await dialog.isVisible())await dialog.getByRole('button',{name:'知道了',exact:true}).click();
 await page.getByRole('button',{name:'创建第一个机器人'}).click();await page.getByLabel('机器人名称').fill('LLBot 本地验收');await page.getByRole('button',{name:'下一步'}).click();
 const protocol=page.getByLabel('QQ 协议端');assert.equal(await protocol.inputValue(),'napcat');await protocol.selectOption('llbot');await page.getByRole('button',{name:'下一步'}).click();await page.getByRole('button',{name:'创建并设置QQ'}).click();
 await page.getByRole('heading',{name:'QQ协议',exact:true}).waitFor();assert.equal(await page.getByLabel('选择协议后端').inputValue(),'llbot');
 await page.screenshot({path:output+'/desktop.png',fullPage:true});await page.setViewportSize({width:390,height:844});await page.getByLabel('选择协议后端').scrollIntoViewIfNeeded();assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);await page.screenshot({path:output+'/mobile.png',fullPage:true});
 await page.getByLabel('选择协议后端').selectOption('napcat');await page.getByRole('button',{name:'切换QQ协议',exact:true}).click();await page.getByText(/切换已排队/).waitFor();assert.equal(await page.getByLabel('选择协议后端').isDisabled(),true);assert.equal((await db.query('SELECT protocol FROM bots'))[0].protocol,'llbot');assert.equal((await db.query("SELECT count(*) AS n FROM commands WHERE kind='switch-protocol' AND status='pending'"))[0].n,1);
 assert.deepEqual(errors,[]);await writeFile(output+'/result.json',JSON.stringify({passed:true,checks:['default NapCat','create LLBot via UI and real API','protocol selector','mobile no overflow','switch queued not prematurely applied','no browser errors']},null,2));console.log('LLBot browser checks passed; screenshots: '+output);
}catch(error){console.log('UI failure',page.url(),(await page.locator('body').innerText()).slice(-2200));await page.screenshot({path:output+'/failure.png',fullPage:true});throw error}finally{await browser.close();await app.close();if(oldOrigin===undefined)delete process.env.APP_ORIGIN;else process.env.APP_ORIGIN=oldOrigin}
