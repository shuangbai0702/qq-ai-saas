import {issueTestInvite} from './helpers/invitations.js';
// Isolated real API + built UI. Hold the initial empty list until upload finishes.
import {chromium} from 'playwright';import assert from 'node:assert/strict';import {mkdtemp,mkdir,writeFile} from 'node:fs/promises';import path from 'node:path';
import {createApp} from '../apps/control-api/src/app.js';
const current=await createApp({root:await mkdtemp(path.resolve('.local/social-browser-race-')),agent:false});
const browser=await chromium.launch({headless:true});const previousOrigin=process.env.APP_ORIGIN;
let release!:()=>void;const held=new Promise<void>(r=>release=r);let initial!:()=>void;const entered=new Promise<void>(r=>initial=r);
try{
 const address=await current.app.listen({host:'127.0.0.1',port:0});process.env.APP_ORIGIN=address;const context=await browser.newContext();
 const auth=await context.request.post(address+'/v1/auth/register',{data:{inviteCode:await issueTestInvite(current.db),email:'race@social.test',name:'迟到回写测试',password:'race-social-123'}});assert.equal(auth.status(),200);
 const created=await context.request.post(address+'/v1/bots',{data:{name:'图库并发验证'}});assert.equal(created.status(),201);const id=(await created.json()).id;
 const page=await context.newPage();let intercepted=false;
 await page.route('**/v1/bots/*/reactions',async route=>{if(route.request().method()==='GET'&&!intercepted){intercepted=true;const response=await route.fetch();initial();await held;await route.fulfill({response})}else await route.continue()});
 await page.goto(address+'/#/bots/'+id+'/config/images');await entered;
 await page.getByLabel('图片使用场景').fill('轻松聊天时笑哭');await page.getByLabel('上传表情PNG').setInputFiles('templates/reactions/laugh.png');await page.getByRole('button',{name:'添加表情图片',exact:true}).click();
 await page.getByRole('img',{name:'轻松聊天时笑哭',exact:true}).waitFor();
 const late=page.waitForResponse(r=>r.url().endsWith('/reactions')&&r.request().method()==='GET');release();await late;
 await page.evaluate(()=>new Promise<void>(r=>requestAnimationFrame(()=>requestAnimationFrame(()=>r()))));
 assert.equal(await page.getByRole('img',{name:'轻松聊天时笑哭',exact:true}).count(),1,'initial empty list must not erase completed upload');
 await mkdir('test-results/social-audit',{recursive:true});await page.screenshot({path:'test-results/social-audit/browser-race.png',fullPage:true});
 await page.getByRole('button',{name:'删除图片',exact:true}).click();await page.getByText('图片已更新，无需重启。发送开关需保存并应用。').waitFor();await page.getByRole('img',{name:'轻松聊天时笑哭',exact:true}).waitFor({state:'detached'});assert.equal((await (await context.request.get(address+'/v1/bots/'+id+'/reactions')).json()).items.length,0,'browser DELETE with no body succeeds and disk metadata disappears');
 await writeFile('test-results/social-audit/browser-race.json',JSON.stringify({passed:true,barrier:'initial empty GET held until POST + fresh GET displayed image',checks:['real API upload','initial response arrives last','new image remains visible','real browser DELETE without empty JSON header succeeds'],qqMessagesSent:0},null,2));
 console.log('PASS real browser stale list cannot overwrite successful upload');
}finally{release();await browser.close();await current.app.close();if(previousOrigin===undefined)delete process.env.APP_ORIGIN;else process.env.APP_ORIGIN=previousOrigin}
