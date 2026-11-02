import {chromium,type Page} from 'playwright';
import assert from 'node:assert/strict';
import {mkdir,writeFile,readFile} from 'node:fs/promises';

// Exercises the migrated painters in the real login UI without account writes.
const out=(process.env.PAPER_QA_OUT||'test-results/paper-integration-20261102')+'/';await mkdir(out,{recursive:true});
const browser=await chromium.launch({headless:true}),checks:any[]=[],errors:string[]=[];
const check=(name:string,passed:boolean,extra:any={})=>{checks.push({name,passed,...extra});assert.ok(passed,name);console.log('PASS '+name);};
const listen=(page:Page)=>page.on('pageerror',e=>errors.push(e.stack||e.message));
const ready=async(page:Page)=>{await page.goto('http://127.0.0.1:5173/',{waitUntil:'domcontentloaded'});await page.waitForFunction(()=>document.body.dataset.paper==='ready');await page.getByRole('heading',{name:'登录账号'}).waitFor();};
const pixels=(page:Page,selector:string)=>page.locator(selector).evaluate((n:HTMLCanvasElement)=>{const d=n.getContext('2d')!.getImageData(0,0,n.width,n.height).data;let hash=2166136261,colours=0,alpha=0;for(let i=0;i<d.length;i++){hash=Math.imul(hash^d[i],16777619);if(i%4===0){if(d[i+3]>1)alpha++;if(d[i+3]>8&&Math.max(d[i],d[i+1],d[i+2])-Math.min(d[i],d[i+1],d[i+2])>20)colours++;}}return {hash,colours,alpha};});
const colourCount=(data:{colours:number})=>data.colours;
const brush=async(page:Page)=>{await page.mouse.move(750,470);await page.mouse.move(790,510,{steps:5});await page.mouse.move(830,530,{steps:5});};
try{
 const context=await browser.newContext({viewport:{width:1280,height:900}}),page=await context.newPage();listen(page);await ready(page);await page.waitForFunction(()=>document.querySelector('.paper-drawing')?.getAttribute('data-wallpaper')==='ready');
 await page.waitForTimeout(80);const original=await pixels(page,'.paper-drawing');await brush(page);await page.waitForTimeout(80);
 const coloured=await pixels(page,'.paper-drawing');check('Round bristled brush reveals the actual wallpaper colour',colourCount(coloured)>colourCount(original)+500);
 await page.screenshot({path:out+'wallpaper-colour.png'});
 await page.waitForTimeout(5900);check('Wallpaper strokes expire by elapsed time without further movement',JSON.stringify(original)===JSON.stringify(await pixels(page,'.paper-drawing')));
 await page.getByRole('button',{name:'持激活码注册',exact:true}).click();await page.waitForTimeout(140);
 const ink=await pixels(page,'.auth-form .switch button .pigment-layer');check('Actual paper button receives coloured absorption inside its face',colourCount(ink)>100);
 await page.screenshot({path:out+'button-pigment.png'});await page.waitForTimeout(1550);
 const faded=await pixels(page,'.auth-form .switch button .pigment-layer');check('Paper pigment clears after its animation',faded.alpha===0);
 await brush(page);await page.waitForTimeout(90);await page.getByRole('button',{name:'暂停纸面动态',exact:true}).click();await page.waitForTimeout(100);
 const frozen=await pixels(page,'.paper-drawing');await page.waitForTimeout(700);check('Pause freezes existing wallpaper strokes',JSON.stringify(frozen)===JSON.stringify(await pixels(page,'.paper-drawing')));
 await page.getByRole('button',{name:'开启纸面动态',exact:true}).click();await page.waitForTimeout(5900);check('Resumed strokes complete their remaining lifetime',JSON.stringify(original)===JSON.stringify(await pixels(page,'.paper-drawing')));
 const hidden=await browser.newContext({viewport:{width:900,height:700}}),hiddenPage=await hidden.newPage();listen(hiddenPage);await ready(hiddenPage);
 await hiddenPage.waitForFunction(()=>document.querySelector('.paper-drawing')?.getAttribute('data-wallpaper')==='ready');await hiddenPage.mouse.move(500,400);await hiddenPage.mouse.move(550,450,{steps:6});
 await hiddenPage.evaluate("Object.defineProperty(document,'hidden',{configurable:true,get:()=>true});document.dispatchEvent(new Event('visibilitychange'))");await hiddenPage.waitForTimeout(80);
 const hiddenPixels=await pixels(hiddenPage,'.paper-drawing');await hiddenPage.waitForTimeout(500);check('Hidden page preserves its drawing clock',JSON.stringify(hiddenPixels)===JSON.stringify(await pixels(hiddenPage,'.paper-drawing')));
 await hiddenPage.evaluate("Object.defineProperty(document,'hidden',{configurable:true,get:()=>false});document.dispatchEvent(new Event('visibilitychange'))");await hiddenPage.waitForTimeout(100);check('Page visibility restoration resumes the existing surfaces',await hiddenPage.locator('.auth .stock-art').count()===4&&await hiddenPage.locator('.paper-motion>.stock-art').count()===1);
 await hidden.close();
 const reduced=await browser.newContext({reducedMotion:'reduce',viewport:{width:390,height:844}}),reducedPage=await reduced.newPage();listen(reducedPage);await ready(reducedPage);
 check('Reduced motion starts with paper dynamics paused',await reducedPage.locator('body').getAttribute('data-paper-motion')==='off');
 await reducedPage.getByRole('button',{name:'持激活码注册',exact:true}).click();check('Reduced motion retains native controls',await reducedPage.getByRole('heading',{name:'注册账号',exact:true}).count()===1);
 await reducedPage.screenshot({path:out+'reduced-mobile.png'});await reduced.close();
 const fallback=await browser.newContext({viewport:{width:1280,height:900}});await fallback.addInitScript(()=>{const get=HTMLCanvasElement.prototype.getContext;(HTMLCanvasElement.prototype as any).getContext=function(type:string,...rest:any[]){return type==='webgl2'?null:(get as any).call(this,type,...rest);};});
 const fallbackPage=await fallback.newPage();listen(fallbackPage);await ready(fallbackPage);check('Missing WebGL keeps real paper surfaces through Canvas2D',await fallbackPage.locator('body').getAttribute('data-paper-renderer')==='canvas2d'&&await fallbackPage.locator('.stock-material').evaluateAll(nodes=>nodes.every(n=>(n as HTMLCanvasElement).width>0)));
 await fallbackPage.getByRole('button',{name:'持激活码注册',exact:true}).click();await fallbackPage.waitForTimeout(150);check('Canvas2D fallback keeps the same pigment interaction',colourCount(await pixels(fallbackPage,'.auth-form .switch button .pigment-layer'))>100);await fallbackPage.screenshot({path:out+'fallback.png'});await fallback.close();
 // Observe the actual shared context at creation, then simulate device context loss.
 const lost=await browser.newContext();await lost.addInitScript(()=>{const get=HTMLCanvasElement.prototype.getContext;(HTMLCanvasElement.prototype as any).getContext=function(type:string,...rest:any[]){const value=(get as any).call(this,type,...rest);if(type==='webgl2'&&value)(window as any).__paperGL=value;return value;};});
 const lostPage=await lost.newPage();listen(lostPage);await ready(lostPage);await lostPage.evaluate(()=>(window as any).__paperGL.getExtension('WEBGL_lose_context').loseContext());await lostPage.waitForFunction(()=>document.body.dataset.paperRenderer==='canvas2d');check('Device context loss falls back without changing forms',await lostPage.getByLabel('邮箱地址').count()===1&&await lostPage.locator('.auth .stock-art').count()===4&&await lostPage.locator('.paper-motion>.stock-art').count()===1);await lost.close();
 const missing=await browser.newContext();await missing.route('**/paper/deepseek-desk.png',route=>route.abort());const missingPage=await missing.newPage();listen(missingPage);await ready(missingPage);await missingPage.waitForFunction(()=>document.querySelector('.paper-drawing')?.getAttribute('data-wallpaper')==='unavailable');check('Wallpaper failure leaves paper UI and native forms usable',await missingPage.locator('.auth .stock-art').count()===4&&await missingPage.locator('.paper-motion>.stock-art').count()===1);await missing.close();
 const delayed=await browser.newContext();let release!:()=>void;const barrier=new Promise<void>(resolve=>{release=resolve;});let requested!:()=>void;const incoming=new Promise<void>(resolve=>{requested=resolve;});
 const wallpaper=await readFile('apps/web/public/paper/deepseek-desk.png');
 await delayed.route('**/paper/deepseek-desk.png',async route=>{requested();await barrier;await route.fulfill({contentType:'image/png',body:wallpaper}).catch(()=>{});});
 const delayedPage=await delayed.newPage();listen(delayedPage);await ready(delayedPage);await incoming;await delayedPage.evaluate(()=>window.dispatchEvent(new PageTransitionEvent('pagehide')));release();await delayedPage.waitForTimeout(200);
 check('Late wallpaper completion cannot revive destroyed layers',await delayedPage.locator('.stock-art').count()===0&&await delayedPage.locator('.paper-drawing').evaluate((c:HTMLCanvasElement)=>c.width===0&&c.height===0));await delayed.close();
 check('No browser exceptions in motion, failure and exit scenarios',errors.length===0,{errors});
}finally{await writeFile(out+'effects-verification.json',JSON.stringify({checks,errors},null,2));await browser.close();}
