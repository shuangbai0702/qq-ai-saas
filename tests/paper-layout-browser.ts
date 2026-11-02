import {chromium,type Page} from 'playwright';
import assert from 'node:assert/strict';
import {mkdir,writeFile} from 'node:fs/promises';
import {defaultConfig} from '../packages/contracts/index';

const out=(process.env.PAPER_QA_OUT||'test-results/paper-corrections-20261102-r11')+'/';await mkdir(out,{recursive:true});
// Keep native scrollbars: a decoration must not create a new scroll range.
const browser=await chromium.launch({headless:true,ignoreDefaultArgs:['--hide-scrollbars']}),checks:any[]=[],errors:string[]=[],calls:any[]=[];
const check=(name:string,passed:boolean,detail:any={})=>{checks.push({name,passed,...detail});assert.ok(passed,JSON.stringify(checks.at(-1)));console.log('PASS '+name);};
const states=['online','starting','stale','stopped','offline','degraded'];
const bot=(index:number)=>{const state=states[index];return {id:'paper-status-'+index,name:'纸面连接样本 '+(index+1),persona_id:'dafeiyu',protocol:'napcat',model_id:'deepseek-chat',runtime_mode:'shared',has_key:index%2===0,revision:1,applied_revision:1,generation:1,desired_state:['stopped','offline'].includes(state)?state:'running',observed_state:state,health:{sampledAt:new Date(Date.now()-(state==='stale'?180000:0)).toISOString(),protocol:{online:state==='online'},ai:{alive:['online','starting'].includes(state),ready:state==='online'}}};};
let count=1;
const workspaces=Array.from({length:12},(_,i)=>({id:'paper-workspace-'+i,name:'纸白工作区 '+(i+1),used:1,bot_limit:4,created_at:'2026-11-02T00:00:00Z',members:[{id:'paper-member-'+i,name:'样本用户',email:'paper-'+i+'@ui.test'}]}));
const ready=async(page:Page)=>{await page.waitForFunction(()=>document.body.dataset.paper==='ready');await page.waitForTimeout(160);};
const fitting=async(page:Page)=>page.locator('.signal,.badge').evaluateAll(nodes=>nodes.flatMap(n=>{
 const box=n.getBoundingClientRect();if(!box.width||!box.height)return [];
 const face=n.querySelector(':scope>.stock-art>use')?.getBoundingClientRect();if(!face)return [{text:n.textContent,reason:'missing-paper'}];
 const walker=document.createTreeWalker(n,NodeFilter.SHOW_TEXT),problems:any[]=[];let text:Node|null;
 while(text=walker.nextNode())if(text.textContent?.trim()&&!text.parentElement?.closest('.stock-art')){
  const range=document.createRange();range.selectNodeContents(text);for(const r of range.getClientRects())if(r.width&&(r.left<face.left+2||r.right>face.right-2||r.top<face.top+2||r.bottom>face.bottom-2))problems.push({text:text.textContent,ink:r.toJSON(),face:face.toJSON()});
 }
 return problems;
}));
try{
 const context=await browser.newContext({viewport:{width:1440,height:900}});
 await context.route('**/v1/**',async route=>{
  const req=route.request(),u=new URL(req.url());calls.push({method:req.method(),path:u.pathname});const match=u.pathname.match(/^\/v1\/bots\/paper-status-(\d)$/),session=u.pathname.match(/^\/v1\/bots\/paper-status-(\d)\/login-session$/);
  const payload=u.pathname==='/v1/auth/me'?{user:{id:'paper-admin',name:'纸面样本',is_admin:true}}:u.pathname==='/v1/admin/workspaces'?{items:workspaces.slice(0,count),total:count,pageSize:20}:u.pathname==='/v1/bots'?{items:states.map((_,i)=>bot(i)),quota:{canCreate:true,used:6,limit:10,available:4}}:match?{bot:bot(Number(match[1])),config:defaultConfig,commands:[],hasKey:Number(match[1])%2===0}:session?{state:'waiting_confirm',hasQr:false}:undefined;
  await route.fulfill({status:payload?200:404,contentType:'application/json',body:JSON.stringify(payload||{})});
 });
 const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
 await page.goto('http://127.0.0.1:5173/#/admin',{waitUntil:'networkidle'});await ready(page);
 for(const width of [1440,768,390])for(const rows of [1,4,12]){
  count=rows;await page.setViewportSize({width,height:844});await page.reload({waitUntil:'networkidle'});await ready(page);await page.evaluate(()=>scrollTo(0,document.documentElement.scrollHeight));await page.mouse.move(width*.65,600);await page.mouse.wheel(0,2000);await page.waitForTimeout(160);
  const trace=await page.evaluate(async()=>{const h=document.querySelector('.table-wrap') as HTMLElement,c=h.querySelector('.paper-row-ink')!;const records=[];
   for(let i=0;i<45;i++){await new Promise(requestAnimationFrame);const r=h.getBoundingClientRect(),p=c.getBoundingClientRect(),bottom=r.top+h.clientTop+h.clientHeight+r.height-h.offsetHeight,right=r.left+h.clientLeft+h.clientWidth+r.width-h.offsetWidth;records.push({y:scrollY,height:document.documentElement.scrollHeight,escape:Math.max(0,p.bottom-bottom,p.right-right),innerScroll:h.scrollHeight-h.clientHeight});}return records;
  });
  check('User-quota bottom stays still at '+width+'px / '+rows+' rows',new Set(trace.map(t=>t.y)).size===1&&new Set(trace.map(t=>t.height)).size===1,{width,rows,range:[Math.min(...trace.map(t=>t.y)),Math.max(...trace.map(t=>t.y))]});
  check('Fractional table print stays inside its true scrollport at '+width+'px / '+rows+' rows',trace.every(t=>t.escape<.02),{escape:Math.max(...trace.map(t=>t.escape)),innerScroll:Math.max(...trace.map(t=>t.innerScroll))});
 }
 await page.screenshot({path:out+'user-quota-bottom-mobile.png'});
 for(const width of [1440,1024,768,390]){
  await page.setViewportSize({width,height:950});await page.goto('http://127.0.0.1:5173/#/bots',{waitUntil:'networkidle'});await ready(page);const issues=await fitting(page);
  check('List connection labels fit their actual paper contours at '+width+'px',issues.length===0,{issues});
  if(width===1440)await page.locator('.bot-rows').screenshot({path:out+'connection-list.png'});
 }
 for(const index of [0,1,2,4]){
  await page.setViewportSize({width:1280,height:900});await page.goto('http://127.0.0.1:5173/#/bots/paper-status-'+index+'/config/faces',{waitUntil:'networkidle'});await page.locator('.runtime-summary').waitFor();await ready(page);const issues=await fitting(page);
  check('Sidebar and heading contain connection state '+states[index],issues.length===0,{issues});
  if(index===1)await page.locator('.runtime-summary').screenshot({path:out+'connection-sidebar.png'});
 }
 for(const index of [1,4])for(const width of [1280,390]){
  await page.setViewportSize({width,height:950});await page.goto('http://127.0.0.1:5173/#/bots/paper-status-'+index+'/qq',{waitUntil:'networkidle'});await page.locator('.qr-info').waitFor();await ready(page);const issues=await fitting(page);
  check('QQ page contains long connection state '+states[index]+' at '+width+'px',issues.length===0,{issues});
  if(index===4&&width===390)await page.locator('.qr-panel').screenshot({path:out+'connection-qq-mobile.png'});
 }
 check('Connection status keeps no overlapping legacy glow dot',await page.locator('.signal.good b').evaluateAll(nodes=>nodes.every(n=>getComputedStyle(n,'::after').content==='none')));
 check('Layout verification issues no business mutations',calls.every(c=>c.method==='GET'));
 check('No browser errors in scroll and connection-layout checks',errors.length===0,{errors});
}finally{await writeFile(out+'layout-verification.json',JSON.stringify({checks,errors,calls},null,2));await browser.close();}
