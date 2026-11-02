import {chromium,type Page} from 'playwright';
import assert from 'node:assert/strict';
import {mkdir,writeFile} from 'node:fs/promises';

// Fixture-backed rendering of the real React memory page. No live account writes.
const out=(process.env.PAPER_QA_OUT||'test-results/paper-refinement-20261102-r9')+'/';await mkdir(out,{recursive:true});
const browser=await chromium.launch({headless:true}),checks:any[]=[],errors:string[]=[],calls:any[]=[];
const check=(name:string,passed:boolean,detail:any={})=>{checks.push({name,passed,...detail});assert.ok(passed,JSON.stringify(checks.at(-1)));console.log('PASS '+name);};
const MiB=1048576,now=Date.now();
const memory=(v:number)=>({processCount:1,pssBytes:v*MiB,ussBytes:v*.65*MiB,swapBytes:106*MiB});
const samples=Array.from({length:14},(_,i)=>{
 const accounts=Array.from({length:3},(_,n)=>({id:'paper-account-'+n,qq:'样本账号 0'+(n+1),name:'纸白工作台 '+(n+1),protocol:'napcat',runtimeMode:'shared',groupId:'paper-group',online:true,status:'online',protocolMemory:memory(14+n*4),dedicatedMemory:memory(0)}));
 const categories=[{id:'shared',name:'共享 AI',...memory(80+i*5)},{id:'protocol',name:'QQ 协议',...memory(42+i)},{id:'api',name:'控制 API',...memory(70)}];if(i===5)(categories[0] as any).pssBytes=null;
 return {sampledAt:new Date(now-(13-i)*30000).toISOString(),details:{view:{overview:{usedBytes:i===7?null:(2500+i*30+Math.sin(i)*100)*MiB,totalBytes:4096*MiB,availableBytes:1300*MiB,swapUsedBytes:(400+i*5)*MiB,zswapPhysicalBytes:(110+i)*MiB,onlineQQ:3,onlineMock:0,runtimeProcesses:4,cpuPercent:12,memoryPressureFull:0,memoryPressureSome:.1},categories,accounts,groups:[{id:'paper-group',...memory(130),onlineMemberCount:3,onlineMembers:accounts,configuredMembers:accounts}],processes:[],coverage:{complete:true,hostFresh:true,hostSampledAt:new Date(now).toISOString()}},host:{memoryPressure:'some avg10=0.10',cpuPressure:'some avg10=0.50'}}};
});
const instrument=()=>{
 const qa=(window as any).__detailsQA={canvases:[] as HTMLCanvasElement[],pending:new Set<number>(),frames:0},create=document.createElement.bind(document),raf=requestAnimationFrame.bind(window),caf=cancelAnimationFrame.bind(window);
 (document as any).createElement=function(tag:string,...args:any[]){const n=(create as any)(tag,...args);if(tag==='canvas')qa.canvases.push(n);return n;};
 window.requestAnimationFrame=fn=>{const id=raf(time=>{qa.pending.delete(id);qa.frames++;fn(time);});qa.pending.add(id);return id;};window.cancelAnimationFrame=id=>{qa.pending.delete(id);caf(id);};
};
const stats=async(page:Page,selector:string)=>page.locator(selector).first().evaluate((c:HTMLCanvasElement)=>{
 const d=c.getContext('2d')!.getImageData(0,0,c.width,c.height).data;let count=0,max=0,red=0;for(let i=3;i<d.length;i+=4){if(d[i]>0)count++;max=Math.max(max,d[i]);if(d[i]>3&&d[i-3]>d[i-2]+20)red++;}return {count,max,red,width:c.width,height:c.height};
});
try{
 const context=await browser.newContext({viewport:{width:1440,height:1050}});await context.addInitScript(instrument);
 await context.route('**/v1/**',async route=>{
  const req=route.request(),url=new URL(req.url());calls.push({method:req.method(),path:url.pathname});
  const payload=url.pathname==='/v1/auth/me'?{user:{id:'paper-ui-user',name:'纸白 UI 样本',is_admin:true}}:url.pathname==='/v1/admin/memory'?{samples}:url.pathname==='/v1/admin/overview'?{bots:[]}:url.pathname==='/v1/bots'?{items:[],quota:{canCreate:true,used:0,limit:4,available:4}}:null;
  await route.fulfill({status:payload?200:404,contentType:'application/json',body:JSON.stringify(payload||{error:'Unexpected UI fixture endpoint'})});
 });
 const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
 await page.goto('http://127.0.0.1:5173/#/admin/memory',{waitUntil:'networkidle'});await page.waitForFunction(()=>document.querySelectorAll('.paper-chart-ink').length===2);await page.waitForTimeout(180);
 check('Real React memory charts receive two ink layers',await page.locator('.memory-trend>svg[role=img]').count()===2&&(await stats(page,'.paper-chart-ink')).count>1500);
 const paths=await page.locator('.memory-trend>svg g>path').evaluateAll(nodes=>nodes.map(n=>n.getAttribute('d')));
 check('Unknown samples retain genuine separate SVG subpaths',(paths[0]!.match(/M/g)||[]).length===2&&(paths[3]!.match(/M/g)||[]).length===2);
 const projection=await page.locator('.memory-trend').first().evaluate(n=>{
  const svg=n.querySelector('svg')!,line=svg.querySelector('line')!,c=n.querySelector('.paper-chart-ink') as HTMLCanvasElement,r=c.getBoundingClientRect(),m=svg.getScreenCTM()!,p=svg.createSVGPoint();p.x=Number(line.getAttribute('x1'));const left=p.matrixTransform(m).x;p.x=Number(line.getAttribute('x2'));const right=p.matrixTransform(m).x;
  const d=c.getContext('2d')!.getImageData(0,0,c.width,c.height).data,s=c.width/r.width;let outside=0,inside=0;for(let i=0;i<d.length;i+=4)if(d[i+3]>15&&Math.max(d[i],d[i+1],d[i+2])-Math.min(d[i],d[i+1],d[i+2])>25){const x=(i/4%c.width)/s+r.left;if(x<left-8||x>right+8)outside++;else inside++;}return {inside,outside};
 });
 check('Rendered ink aligns with native SVG axes despite aspect-ratio letterboxing',projection.inside>200&&projection.outside===0,projection);
 // Off-screen charts are painted on demand by the shared owner.
 await page.locator('.memory-trend').last().scrollIntoViewIfNeeded();await page.waitForTimeout(100);await page.locator('.memory-trend').first().scrollIntoViewIfNeeded();await page.waitForTimeout(100);
 const inkMaterial=await page.locator('.memory-trend').evaluateAll(figures=>figures.map(n=>{
  const svg=n.querySelector('svg')!,c=n.querySelector('.paper-chart-ink') as HTMLCanvasElement,r=c.getBoundingClientRect(),m=svg.getScreenCTM()!,scale=c.width/r.width,segments:number[][]=[];let previous:number[]|undefined;
  for(const path of svg.querySelectorAll('g>path')){previous=undefined;for(const match of (path.getAttribute('d')||'').matchAll(/([ML])\s*(-?[\d.]+)[, ]+(-?[\d.]+)/g)){
   const p=new DOMPoint(Number(match[2]),Number(match[3])).matrixTransform(m),point=[p.x-r.left,p.y-r.top];if(match[1]==='L'&&previous)segments.push([...previous,...point]);previous=point;
  }}
  const d=c.getContext('2d')!.getImageData(0,0,c.width,c.height).data,hues={green:0,orange:0,purple:0},alphas=new Set<number>();let awayFromLine=0;
  for(let i=0;i<d.length;i+=4)if(d[i+3]>15){
   if(d[i+1]>d[i]+10&&d[i+1]>d[i+2]+6)hues.green++;if(d[i]>d[i+1]+15&&d[i+1]>d[i+2]+15)hues.orange++;if(d[i+2]>d[i]+15&&d[i]>d[i+1]+6)hues.purple++;alphas.add(d[i+3]);
   const x=(i/4%c.width+.5)/scale,y=(Math.floor(i/4/c.width)+.5)/scale;
   const distance=Math.min(...segments.map(([ax,ay,bx,by])=>{const dx=bx-ax,dy=by-ay,t=Math.max(0,Math.min(1,((x-ax)*dx+(y-ay)*dy)/(dx*dx+dy*dy)));return Math.hypot(x-ax-t*dx,y-ay-t*dy);}));if(distance>9)awayFromLine++;
  }
  return {hues,opacityLevels:alphas.size,awayFromLine,strokes:[...svg.querySelectorAll('g>path')].map(path=>getComputedStyle(path).stroke),legends:[...n.querySelectorAll('.memory-legend i')].map(i=>getComputedStyle(i).backgroundColor)};
 }));
 check('The shared granular chart material keeps each series original colour',inkMaterial.every(s=>s.hues.green>100&&s.hues.orange>100&&s.opacityLevels>20&&(s.strokes.length<3||s.hues.purple>100)),{inkMaterial});
 check('Chart legend colours agree with their actual series',inkMaterial.every(s=>JSON.stringify(s.legends)===JSON.stringify(s.strokes)),{inkMaterial});
 check('Chart ink stays near measured traces without any under-line area',inkMaterial.every(s=>s.awayFromLine===0),{inkMaterial});
 await page.locator('.memory-trend>svg').first().hover();await page.waitForTimeout(250);
 check('Ink hover never changes measured sample coordinates',JSON.stringify(paths)===JSON.stringify(await page.locator('.memory-trend>svg g>path').evaluateAll(nodes=>nodes.map(n=>n.getAttribute('d')))));
 await page.locator('.memory-monitor>.panel').first().screenshot({path:out+'project-chart.png'});
 const online=page.locator('.memory-monitor>.panel').filter({has:page.getByRole('heading',{name:'在线 QQ · 3 个真实在线',exact:true})});
 const row=online.locator('tbody tr').nth(1);await row.hover();await page.waitForTimeout(330);const grain=await stats(page,await online.locator('.paper-row-ink').evaluate(n=>{n.id='qa-online-ink';return '#'+n.id;}));
 check('Ordinary real table rows use one shallow print, without individual paper cards',grain.count>200&&grain.max<65&&await row.locator(':scope>.stock-art').count()===0,grain);
 const continuity=await row.evaluate(n=>{
  const c=n.closest('.table-wrap')!.querySelector(':scope>.paper-row-ink') as HTMLCanvasElement,cr=c.getBoundingClientRect(),r=n.querySelector('b')!.getBoundingClientRect(),s=c.width/cr.width,ctx=c.getContext('2d')!;
  const d=ctx.getImageData(Math.max(0,Math.round((r.left-cr.left)*s)),Math.max(0,Math.round((r.top-cr.top)*s)),Math.ceil(r.width*s),Math.ceil(r.height*s)).data;return {count:Array.from(d).filter((v,i)=>i%4===3&&v>0).length,text:r.toJSON(),canvas:cr.toJSON(),scale:s,row:n.getBoundingClientRect().toJSON()};
 });
 await online.screenshot({path:out+'project-row-debug.png'});
 check('Row texture continues beneath text instead of rectangular text cutouts',continuity.count>50,{continuity});
 const rowBox=await row.boundingBox();await page.mouse.move(rowBox!.x+rowBox!.width*.7,rowBox!.y+rowBox!.height*.5,{steps:5});await page.waitForTimeout(120);
 check('Table hover does not stack the parent paper brush response',await online.locator(':scope>.stock-art .brush-layer').evaluate((c:HTMLCanvasElement)=>!c.getContext('2d')!.getImageData(0,0,c.width,c.height).data.some((v,i)=>i%4===3&&v>0)));
 await online.screenshot({path:out+'project-row-hover.png'});
 const advanced=page.locator('.memory-advanced'),summary=advanced.locator(':scope>summary');await summary.scrollIntoViewIfNeeded();await page.waitForTimeout(140);
 const aligned=await advanced.evaluate(n=>{const a=n.getBoundingClientRect(),b=n.querySelector('summary>.stock-art')!.getBoundingClientRect();return {ok:Math.abs(a.left-b.left)<=2&&Math.abs(a.top-b.top)<=2&&Math.abs(a.width-b.width)<=2&&Math.abs(a.height-b.height)<=2,node:a.toJSON(),art:b.toJSON(),opacity:getComputedStyle(n.querySelector('.stock-material')!).opacity};});
 await advanced.screenshot({path:out+'project-disclosure-debug.png'});
 check('Closed native disclosure keeps its translucent paper visible and aligned',aligned.ok&&aligned.opacity==='0.76',aligned);
 await advanced.screenshot({path:out+'project-disclosure-closed.png'});await summary.click();await page.waitForTimeout(150);
 check('Opening disclosure retains native semantics and makes its sheet opaque',await advanced.getAttribute('open')!==null&&await summary.locator('.stock-material').evaluate(c=>getComputedStyle(c).opacity==='1'));
 await summary.focus();await page.keyboard.press('Enter');await page.waitForTimeout(100);check('Keyboard still closes the native disclosure',await advanced.getAttribute('open')===null);
 // A dynamically mounted native dialog exercises the same portal/textarea boundary.
 await page.evaluate(()=>{const dialog=document.createElement('dialog');dialog.className='edit-dialog';dialog.id='qa-paper-dialog';dialog.innerHTML='<h2>纸面细节验证</h2><label class="field"><span>文字</span><textarea aria-label="纸面细节文字" rows="3">纸边轻轻压下，文字仍旧清楚。\n选区保留真正的原生选择。</textarea></label><button class="secondary" type="button">关闭样本</button>';document.body.append(dialog);dialog.showModal();});
 const field=page.getByLabel('纸面细节文字',{exact:true});await field.focus();await page.waitForTimeout(220);const focus=await stats(page,'.paper-focus-ink');
 check('Native input focus uses a narrow pencil impression rather than a colour wash',focus.count>20&&focus.count<2500&&focus.red<100,focus);
 check('Focus layer is visible in the modal top layer',await page.locator('#qa-paper-dialog>.paper-focus-ink').count()===1&&await field.evaluate(n=>getComputedStyle(n).outlineStyle==='none'));
 const value=await field.inputValue();await field.evaluate((n:HTMLTextAreaElement)=>{n.setSelectionRange(2,16);n.dispatchEvent(new Event('select',{bubbles:true}));});await page.waitForTimeout(220);
 check('Selected native text keeps its actual value and range',await field.inputValue()===value&&await field.evaluate((n:HTMLTextAreaElement)=>n.selectionStart===2&&n.selectionEnd===16));
 check('The open printed selection also renders above a native modal',await page.locator('#qa-paper-dialog>.paper-selection-ink').count()===1&&(await stats(page,'.paper-selection-ink')).count>100);
 await page.screenshot({path:out+'project-input-selection.png'});
 await field.evaluate((n:HTMLTextAreaElement)=>{n.setSelectionRange(4,4);n.dispatchEvent(new Event('select',{bubbles:true}));});await page.waitForTimeout(350);
 check('Clearing a native range removes every selection mark',(await stats(page,'.paper-selection-ink')).count===0);
 await page.evaluate(()=>{const d=document.querySelector('#qa-paper-dialog') as HTMLDialogElement;d.close();d.remove();});await page.waitForTimeout(180);
 check('Closing a portal cannot strand overlays in its removed DOM',await page.locator('body>.paper-focus-ink,body>.paper-selection-ink').count()===2);
 await page.locator('.memory-note').scrollIntoViewIfNeeded();await page.locator('.memory-note').evaluate(n=>{const r=document.createRange();r.selectNodeContents(n);getSelection()!.removeAllRanges();getSelection()!.addRange(r);});await page.waitForTimeout(200);
 check('Ordinary document text uses its real native Range',await page.evaluate(()=>getSelection()!.toString().includes('进程 PSS'))&&(await stats(page,'.paper-selection-ink')).count>100);
 await page.evaluate(()=>scrollBy(0,-35));await page.waitForTimeout(180);check('Selection remeasures after scroll',(await stats(page,'.paper-selection-ink')).count>100);
 await page.getByRole('button',{name:'暂停纸面动态',exact:true}).click();await page.waitForTimeout(100);await page.evaluate(()=>getSelection()!.removeAllRanges());await page.waitForTimeout(100);
 check('Selection clears immediately when procedural time is paused',(await stats(page,'.paper-selection-ink')).count===0);
 const frames=await page.evaluate(()=>(window as any).__detailsQA.frames);await page.waitForTimeout(120);
 check('The shared paused owner leaves no animation loop',frames===await page.evaluate(()=>(window as any).__detailsQA.frames)&&await page.evaluate(()=>(window as any).__detailsQA.pending.size)===0);
 await page.getByRole('button',{name:'开启纸面动态',exact:true}).click();
 for(const width of [320,390,768,1440]){
  await page.setViewportSize({width,height:950});await page.waitForTimeout(130);check('Actual memory page has no viewport overflow at '+width,await page.evaluate(()=>document.documentElement.scrollWidth===innerWidth));
 }
 await row.hover();await page.waitForTimeout(200);await page.setViewportSize({width:390,height:844});await page.waitForTimeout(150);
 const scroll=online.locator('.table-wrap');await scroll.evaluate(n=>{n.scrollLeft=n.scrollWidth;});await page.waitForTimeout(150);
 check('Long table ink remains clipped after horizontal scrolling',await online.locator('.paper-row-ink').evaluate(n=>{const a=n.getBoundingClientRect(),b=n.parentElement!.getBoundingClientRect();return Math.abs(a.left-b.left)<3&&a.width<=b.width;}));
 await row.evaluate(n=>{const r=document.createRange();r.selectNodeContents(n);getSelection()!.removeAllRanges();getSelection()!.addRange(r);});await page.waitForTimeout(220);
 const selectedClip=await scroll.evaluate(n=>{
  const c=document.querySelector('.paper-selection-ink') as HTMLCanvasElement,r=n.getBoundingClientRect(),d=c.getContext('2d')!.getImageData(0,0,c.width,c.height).data,s=c.width/innerWidth;let outside=0,inside=0;
  for(let i=3;i<d.length;i+=4)if(d[i]){const x=((i-3)/4%c.width)/s;if(x<r.left||x>r.right)outside++;else inside++;}return {outside,inside};
 });
 check('Native selection ink respects the scrolled table clip',selectedClip.inside>100&&selectedClip.outside===0,selectedClip);
 await page.screenshot({path:out+'project-mobile-table.png'});
 await page.emulateMedia({forcedColors:'active'});await page.waitForTimeout(120);
 check('Forced colours restores readable native chart paths',await page.locator('.paper-chart-ink').first().evaluate(n=>getComputedStyle(n).display==='none')&&await page.locator('.memory-trend svg g>path').first().evaluate(n=>getComputedStyle(n).opacity==='1'));
 await page.emulateMedia({forcedColors:'none'});
 for(let i=0;i<3;i++){await page.evaluate(()=>{location.hash='/bots';});await page.waitForTimeout(160);await page.evaluate(()=>{location.hash='/admin/memory';});await page.waitForTimeout(220);}
 check('Navigation rebinds one overlay per actual table and chart',await page.locator('.paper-chart-ink').count()===2&&await page.locator('.paper-table-host').evaluateAll(nodes=>nodes.every(n=>n.querySelectorAll(':scope>.paper-row-ink').length===1)));
 check('This visual verification never sends business mutations',calls.every(c=>c.method==='GET'));
 await page.evaluate(()=>{window.dispatchEvent(new PageTransitionEvent('pagehide'));window.dispatchEvent(new PageTransitionEvent('pagehide'));});await page.waitForTimeout(100);
 const cleanup=await page.evaluate(()=>({overlays:document.querySelectorAll('.paper-row-ink,.paper-focus-ink,.paper-selection-ink,.paper-chart-ink,.stock-art').length,live:(window as any).__detailsQA.canvases.filter((c:HTMLCanvasElement)=>c.width&&c.height).length,pending:(window as any).__detailsQA.pending.size}));
 check('Exit releases every new layer and cached impression',cleanup.overlays===0&&cleanup.live===0&&cleanup.pending===0,cleanup);
 check('No browser errors in real-page material adaptation',errors.length===0,{errors});
}finally{await writeFile(out+'project-details-verification.json',JSON.stringify({checks,errors,calls},null,2));await browser.close();}
