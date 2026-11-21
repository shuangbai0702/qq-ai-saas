import {chromium} from 'playwright';
import assert from 'node:assert/strict';
import {mkdtemp,writeFile,rm} from 'node:fs/promises';
import path from 'node:path';
import {createApp} from '../apps/control-api/src/app.js';
import {issueInvitation} from '../packages/shared/invitations.js';
import {loadKey,encrypt} from '../packages/shared/security.js';
import {platformSecretPurpose} from '../apps/control-api/src/platform-model.js';

const root=await mkdtemp(path.resolve('.local/referral-browser-')),key=await loadKey(root);
await writeFile(path.join(root,'platform-model-secret'),encrypt(key,platformSecretPurpose,JSON.stringify({primary:'fixture',fallback:'fixture'})));
const {app,db}=await createApp({root,databaseUrl:'',agent:false});console.log('Fixture ready');
const browser=await chromium.launch({headless:true});const previous=process.env.APP_ORIGIN;
try{
 const address=await app.listen({host:'127.0.0.1',port:0});process.env.APP_ORIGIN=address;
 const activation=(await issueInvitation(db,null,{maxUses:3})).code;
 const context=await browser.newContext({viewport:{width:390,height:844},permissions:['clipboard-read','clipboard-write']});
 context.setDefaultTimeout(15000);console.log('Context ready');
 const owner=await context.request.post(address+'/v1/auth/register',{data:{email:'owner@referral-browser.test',name:'邀请人',password:'fixture-pass',inviteCode:activation}});assert.equal(owner.status(),200);
 const page=await context.newPage(),errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto(address+'/#/referrals');console.log('Loaded page');await page.getByRole('heading',{name:'邀请奖励',exact:true}).waitFor();
 assert.equal(await page.getByPlaceholder('填入管理员提供的可用激活码').count(),0);
 await page.getByRole('dialog').getByRole('button',{name:'知道了',exact:true}).click();
 const later=page.getByRole('dialog').getByRole('button',{name:'稍后再说',exact:true});try{await later.waitFor({timeout:2000});await later.click()}catch(error){if((error as Error).name!=='TimeoutError')throw error}
 console.log('Generating');await page.getByRole('button',{name:'生成邀请链接',exact:true}).click();
 const field=page.getByLabel('我的邀请链接');await field.waitFor();const first=await field.inputValue();
 const parse=(link:string)=>{const url=new URL(link);assert.match(url.hash,/^#\/invite\/[A-Za-z0-9_-]{24}\?/);const token=new URLSearchParams(url.hash.split('?')[1]).get('activation');assert.ok(token);assert.match(token,/^[A-Za-z0-9+/=]+\.[A-Za-z0-9+/=]+\.[A-Za-z0-9+/=]+$/);return token};
 const original=parse(first);await page.getByRole('button',{name:'重新生成邀请链接',exact:true}).click();
 await page.waitForFunction(old=>(document.querySelector('input[readonly]') as HTMLInputElement)?.value!==old,first);
 const link=await field.inputValue();assert.notEqual(parse(link),original);
 assert.equal((await db.query('SELECT used_count FROM invitations'))[0].used_count,1);
 console.log('Copying');await page.getByRole('button',{name:'复制邀请链接',exact:true}).click();await page.getByText('邀请链接已复制',{exact:true}).waitFor();
 assert.equal(await page.evaluate(()=>navigator.clipboard.readText()),link);
 const guest=await browser.newContext(),guestPage=await guest.newPage();guest.setDefaultTimeout(15000);await guestPage.goto(link);console.log('Guest link loaded');
 await guestPage.getByRole('heading',{name:'注册账号',exact:true}).waitFor();
 await guestPage.getByLabel('你的称呼').fill('好友');await guestPage.getByLabel('邮箱地址').fill('friend@referral-browser.test');await guestPage.getByLabel('密码',{exact:true}).fill('fixture-pass');
 await guestPage.getByRole('button',{name:'创建账号',exact:true}).click();await guestPage.getByRole('heading',{name:'邀请奖励',exact:true}).waitFor();
 assert.equal((await db.query('SELECT used_count FROM invitations'))[0].used_count,2);assert.equal((await db.query('SELECT count(*)::int AS count FROM referral_rewards'))[0].count,1);
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);assert.deepEqual(errors,[]);
 console.log('Referral browser passed: authenticated generation, encoded URL, regeneration and clipboard do not consume, real browser signup commits once, mobile layout');
}catch(error){console.error(error);throw error}finally{await browser.close();await app.close();await rm(root,{recursive:true,force:true});if(previous===undefined)delete process.env.APP_ORIGIN;else process.env.APP_ORIGIN=previous}
