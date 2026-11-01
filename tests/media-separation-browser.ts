import {chromium} from 'playwright';import assert from 'node:assert/strict';import {mkdtemp,mkdir,writeFile} from 'node:fs/promises';import path from 'node:path';import {execFile} from 'node:child_process';import {promisify} from 'node:util';
import {createApp} from '../apps/control-api/src/app.js';import {issueTestInvite} from './helpers/invitations.js';
const current=await createApp({root:await mkdtemp(path.resolve('.local/media-browser-')),agent:false}),browser=await chromium.launch({headless:true}),oldOrigin=process.env.APP_ORIGIN,output='test-results/media-separation';await mkdir(output,{recursive:true});
try{
 const address=await current.app.listen({host:'127.0.0.1',port:0});process.env.APP_ORIGIN=address;
 const context=await browser.newContext({viewport:{width:1440,height:1000}});
 assert.equal((await context.request.post(address+'/v1/auth/register',{data:{inviteCode:await issueTestInvite(current.db),email:'media@browser.test',password:'media-browser-123',name:'媒体分区验收'}})).status(),200);
 const id=(await(await context.request.post(address+'/v1/bots',{data:{name:'表情与图库验收'}})).json()).id;
 await promisify(execFile)(path.resolve(`references/astrbot/.venv/${process.platform==='win32'?'Scripts/python.exe':'bin/python'}`),['tests/media_separation_test.py','--seed',path.join(current.root,'bots',id,'behavior.sqlite')],{windowsHide:true});
 const page=await context.newPage(),errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));await page.goto(address+`/#/bots/${id}/config/faces`);
 await page.getByRole('heading',{name:'QQ内置表情',exact:true}).waitFor();await page.getByRole('checkbox',{name:/允许QQ内置表情/}).uncheck();
 await page.getByRole('link',{name:'聊天表情包',exact:true}).click();await page.getByRole('button',{name:'审核收录',exact:true}).waitFor();
 await page.locator('img[src*="/meme-packs/blue-fat-fish/"]').first().waitFor();assert.equal(await page.getByText(/另含3张内置基础图/).count(),0);
 const fishImage=await context.request.get(new URL(await page.locator('img[src*="/meme-packs/blue-fat-fish/"]').first().getAttribute('src') as string,address).href);assert.equal(fishImage.status(),200);assert.match(fishImage.headers()['content-type'],/image\/gif/);assert.match((await fishImage.body()).subarray(0,6).toString(),/^GIF8[79]a$/);
 await page.getByRole('checkbox',{name:/自动收集聊天表情包/}).check();await page.getByRole('checkbox',{name:/从机器人允许的全部群收集/}).uncheck();await page.getByLabel('允许收集的群号').fill('12345');await page.getByLabel('允许收集的群号').pressSequentially(',54321');assert.equal(await page.getByLabel('允许收集的群号').inputValue(),'12345,54321');
 await page.getByRole('button',{name:'保存并应用',exact:true}).click();await page.getByText(/版本 2 已保存/).waitFor();
 await page.getByRole('button',{name:'审核收录',exact:true}).click();await page.getByRole('dialog').getByLabel('这张表情适合什么场景').fill('大家轻松开玩笑时回应');await page.getByRole('dialog').getByRole('button',{name:'保存修改',exact:true}).click();await page.getByText('已收录 · 大家轻松开玩笑时回应',{exact:true}).waitFor();
 await page.screenshot({path:output+'/sticker-inbox-desktop.png',fullPage:true});
 await page.getByRole('link',{name:'普通图库',exact:true}).click();await page.getByRole('heading',{name:'普通图库',exact:true}).waitFor();assert.equal(await page.getByText('大家轻松开玩笑时回应',{exact:true}).count(),0);
 await page.getByLabel('图片使用场景',{exact:true}).fill('角色的日常照片');await page.getByLabel('上传表情PNG',{exact:true}).setInputFiles('templates/reactions/laugh.png');await page.getByRole('button',{name:'添加图库图片',exact:true}).click();await page.getByRole('img',{name:'角色的日常照片',exact:true}).waitFor();
 await page.getByRole('checkbox',{name:/允许应请求发送普通图库图片/}).check();await page.getByRole('button',{name:'保存并应用',exact:true}).click();await page.getByText(/版本 3 已保存/).waitFor();await page.reload();await page.getByRole('img',{name:'角色的日常照片',exact:true}).waitFor();assert.equal(await page.getByRole('checkbox',{name:/允许应请求发送普通图库图片/}).isChecked(),true);
 const detail=await(await context.request.get(address+'/v1/bots/'+id)).json();assert.equal(detail.config.behavior.qqFaces,false);assert.equal(detail.config.behavior.stickerCollect,true);assert.deepEqual(detail.config.behavior.stickerGroups,['12345','54321']);assert.equal(detail.config.behavior.galleryEnabled,true);assert.equal(detail.config.behavior.memeEnabled,true,'native faces must not disable existing meme sending');
 await page.screenshot({path:output+'/gallery-desktop.png',fullPage:true});
 await page.getByRole('link',{name:'聊天表情包',exact:true}).click();await page.getByText('已收录 · 大家轻松开玩笑时回应',{exact:true}).waitFor();assert.equal(await page.getByRole('img',{name:'角色的日常照片',exact:true}).count(),0);
 await page.setViewportSize({width:390,height:844});await page.screenshot({path:output+'/sticker-inbox-mobile.png',fullPage:true});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
 page.once('dialog',d=>d.accept());await page.getByRole('button',{name:'移除表情',exact:true}).click();await page.getByText('尚无收集记录。先开启收集并授权群，再接收群友发送的表情。',{exact:true}).waitFor();
 assert.deepEqual(errors,[]);await writeFile(output+'/browser.json',JSON.stringify({passed:true,checks:['three separate sections','independent toggles persist','scoped collection configuration','review and remove via real API','gallery upload and persistence','gallery and sticker inbox separate','mobile no overflow'],qqMessagesSent:0,errors},null,2));console.log('PASS media separation browser');
}finally{await browser.close();await current.app.close();if(oldOrigin===undefined)delete process.env.APP_ORIGIN;else process.env.APP_ORIGIN=oldOrigin}
