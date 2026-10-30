import{chromium}from'playwright';import{writeFile}from'node:fs/promises';import assert from'node:assert/strict';
const webUrl=process.env.WEB_URL||'http://127.0.0.1:5173';
const password=process.env.BOOTSTRAP_ADMIN_PASSWORD;if(!password)throw new Error('需要本地验收管理员密码环境变量');
const browser=await chromium.launch();const page=await browser.newPage({viewport:{width:1440,height:1000}});
try{await page.goto(webUrl);await page.getByLabel('邮箱地址').fill('owner@fishbot.local');await page.getByLabel('密码',{exact:true}).fill(password);await page.getByRole('button',{name:'登录工作区'}).click();await page.getByRole('heading',{name:/我的机器人/}).waitFor();
 const id=await page.evaluate(async()=>{const existing=await(await fetch('/v1/bots')).json();const owned=existing.items.find((b:any)=>b.name==='管理员并发验收'&&b.protocol==='mock');if(owned)return owned.id;return(await(await fetch('/v1/bots',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:'管理员并发验收',protocol:'mock'})})).json()).id});
 await page.goto(`${webUrl}/#/bots/${id}/config`);await page.getByLabel('System Prompt').waitFor();await page.getByLabel('System Prompt').fill('旧编辑器内容不允许覆盖最新配置');
 const updated=await page.evaluate(async id=>{const d=await(await fetch(`/v1/bots/${id}`)).json();const r=await fetch(`/v1/bots/${id}/config`,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:d.bot.name,expectedUin:d.bot.expected_uin,config:{...d.config,systemPrompt:'大肥鱼本地演示：简短自然聊天，按QQ ID区分群友。'},expectedRevision:d.bot.revision})});return r.status},id);assert.equal(updated,200);
 // Wait for the background refresh: it must not replace the editor's base revision.
 await page.waitForTimeout(5500);await page.getByRole('button',{name:'保存并应用'}).click();await page.getByText('配置已被其他操作修改，请刷新后重试').waitFor();
 const prompt=await page.evaluate(async id=>(await(await fetch(`/v1/bots/${id}`)).json()).config.systemPrompt,id);assert.ok(!prompt.includes('旧编辑器'));
 await page.goto(`${webUrl}/#/admin`);await page.getByRole('heading',{name:'实例管理',exact:true}).waitFor();await page.getByText('服务器资源',{exact:true}).waitFor();await page.screenshot({path:'test-results/browser-admin.png',fullPage:true});await writeFile('test-results/admin-browser-results.json',JSON.stringify({passed:true,checks:['local administrator login','administrator API and resource page','editor revision remains fixed across background poll','conflict rejected without overwriting saved prompt']}));console.log('Admin and concurrent editor browser checks passed');
}finally{await browser.close()}
