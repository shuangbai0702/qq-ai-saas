import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {readFile,mkdtemp} from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import {spawnSync} from 'node:child_process';
import {chromium} from 'playwright';
import {api,ApiError} from '../apps/web/src/api';

test('request cancellation and timeout cover response bodies and preserve uncertain writes',async()=>{
 const originalFetch=globalThis.fetch,originalTimer=globalThis.setTimeout;
 try{
  const owner=new AbortController();let calls=0;
  globalThis.fetch=async(_url,options)=>{calls++;return {json:()=>new Promise((_resolve,reject)=>options!.signal!.addEventListener('abort',()=>reject(new DOMException('aborted','AbortError'))))} as Response};
  const pending=api('/probe',undefined,undefined,owner.signal);await Promise.resolve();owner.abort();await assert.rejects(pending,{name:'AbortError'});
  globalThis.setTimeout=((fn:any,ms:any,...args:any[])=>originalTimer(fn,ms===30000?10:ms,...args)) as typeof setTimeout;
  await assert.rejects(api('/write',{value:1}),e=>e instanceof ApiError&&e.status===0&&e.message.includes('请求超时')&&e.message.includes('勿重复操作'));
  assert.equal(calls,2,'never retry an uncertain write automatically');
 }finally{globalThis.fetch=originalFetch;globalThis.setTimeout=originalTimer}
});

test('mobile bootstrap and isolated decoration recover from real failure paths',async t=>{
 let dist=process.env.MOBILE_WEB_DIST;
 if(!dist){dist=await mkdtemp(path.join(os.tmpdir(),'fishbot-mobile-'));const result=spawnSync(process.execPath,['node_modules/vite/bin/vite.js','build','--config','apps/web/vite.config.ts','--outDir',dist],{encoding:'utf8',windowsHide:true});assert.equal(result.status,0,result.stdout+result.stderr)}
 const root=path.resolve(dist);
 const server=createServer(async(req,res)=>{try{const pathname=new URL(req.url!,'http://localhost').pathname;const file=path.resolve(root,'.'+(pathname==='/'?'/index.html':pathname));if(!file.startsWith(root+path.sep))throw Error();const body=await readFile(file);res.setHeader('Content-Type',file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':file.endsWith('.html')?'text/html':'application/octet-stream');res.end(body)}catch{res.statusCode=404;res.end()}});
 await new Promise<void>(resolve=>server.listen(0,'127.0.0.1',resolve));const base='http://127.0.0.1:'+(server.address() as any).port;
 const browser=await chromium.launch({headless:true});
 async function scenario(name:string,options:{desktop?:boolean;init?:string|(()=>void);me?:number;blocked?:RegExp},check:(page:any,requests:string[],errors:string[])=>Promise<void>){
  await t.test(name,async()=>{const context=await browser.newContext({viewport:options.desktop?{width:1440,height:900}:{width:390,height:844},hasTouch:!options.desktop});const page=await context.newPage();const errors:string[]=[],requests:string[]=[];page.on('pageerror',(e:Error)=>errors.push(e.message));page.on('request',(r:any)=>requests.push(r.url()));if(options.init)await page.addInitScript(options.init);
   await page.route('**/v1/**',async(route:any)=>{const url=new URL(route.request().url());const status=url.pathname==='/v1/auth/me'?options.me||401:200;const data=url.pathname==='/v1/auth/me'?status===200?{user:{id:'isolated-user',name:'隔离测试',is_admin:false}}:{error:'test service unavailable'}:{items:[],quota:{canCreate:true,used:0,limit:1,available:1}};await route.fulfill({status,json:data})});
   if(options.blocked)await page.route(options.blocked,(route:any)=>route.abort());
   try{await page.goto(base);await check(page,requests,errors)}finally{await context.close()}
  });
 }
 try{
  await scenario('cold mobile loads auth without workspace or decoration',{},async(p,requests,errors)=>{await p.getByRole('heading',{name:'登录账号',exact:true}).waitFor();assert(!requests.some(u=>/\/assets\/(workspace|start)-/.test(u)));assert.equal(await p.locator('#paper-decoration').count(),0);assert.equal(await p.locator('body.paper-app').count(),1);assert.deepEqual(errors,[]);assert(await p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth))});
  await scenario('legacy AbortSignal helpers absent still performs auth request',{init:()=>{Object.defineProperty(AbortSignal,'timeout',{value:undefined});Object.defineProperty(AbortSignal,'any',{value:undefined})}},async(p,requests,errors)=>{await p.getByRole('heading',{name:'登录账号',exact:true}).waitFor();assert(requests.some(u=>u.endsWith('/v1/auth/me')));assert.deepEqual(errors,[])});
  await scenario('missing observer cannot blank desktop login',{desktop:true,init:()=>{Object.defineProperty(window,'ResizeObserver',{value:undefined})}},async(p,_r,errors)=>{await p.getByRole('heading',{name:'登录账号',exact:true}).waitFor();await p.waitForTimeout(2300);assert.equal(await p.locator('#paper-decoration').count(),0);assert.deepEqual(errors,[])});
  await scenario('animation construction failure cleans up and preserves login',{desktop:true,init:"Object.defineProperty(window,'ResizeObserver',{value:class{constructor(){throw Error('deliberate decoration failure')}}})"},async(p)=>{await p.getByRole('heading',{name:'登录账号',exact:true}).waitFor();await p.waitForTimeout(2500);assert.equal(await p.locator('body.paper-app').count(),1,JSON.stringify(await p.evaluate(()=>({text:document.body.innerText,html:document.body.className}))) );assert(await p.getByRole('heading',{name:'登录账号',exact:true}).isVisible());assert.equal(await p.locator('[data-paper-owned]').count(),0)});
  await scenario('API 503 offers explicit retry instead of showing logged-out form',{me:503},async p=>{await p.getByRole('button',{name:'重试连接'}).waitFor();assert.equal(await p.getByRole('heading',{name:'登录账号',exact:true}).count(),0);await p.route('**/v1/auth/me',(route:any)=>route.fulfill({status:401,json:{error:'unauthorized'}}));await p.getByRole('button',{name:'重试连接'}).click();await p.getByRole('heading',{name:'登录账号',exact:true}).waitFor()});
  await scenario('blocked entry leaves readable static loading and recovery',{blocked:/\/assets\/index-.*\.js/},async p=>{await p.getByRole('link',{name:'重新加载',exact:true}).waitFor();assert((await p.locator('#root').innerText()).includes('正在加载'))});
  await scenario('authenticated workspace keeps normal navigation and logout',{me:200,init:()=>localStorage.setItem('fishbot:announcement:isolated-user:2026-11-17-server-recovery','read')},async(p,_r,errors)=>{await p.getByRole('heading',{name:/我的机器人/}).waitFor();await p.getByRole('link',{name:'公告',exact:true}).click();await p.getByRole('heading',{name:'公告',exact:true}).waitFor();await p.getByRole('button',{name:'退出登录',exact:true}).click();await p.getByRole('heading',{name:'登录账号',exact:true}).waitFor();assert.deepEqual(errors,[])});
  await scenario('blocked workspace chunk offers reload without blank root',{me:200,blocked:/\/assets\/workspace-.*\.js/},async p=>{await p.getByRole('button',{name:'重新加载',exact:true}).waitFor();assert((await p.locator('#root').innerText()).includes('页面资源加载失败'))});
 }finally{await browser.close();await new Promise<void>(resolve=>server.close(()=>resolve()))}
});
