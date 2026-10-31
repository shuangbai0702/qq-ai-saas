import {chromium} from 'playwright';
import assert from 'node:assert/strict';
 import {build} from 'esbuild';
 const bundle=await build({stdin:{contents:"import React from 'react';import{createRoot}from 'react-dom/client';import{QQPanel}from './apps/web/src/qq-panel';createRoot(document.getElementById('root')).render(React.createElement(QQPanel,{id:'test',mock:false,online:false,onError:console.error}));",resolveDir:process.cwd(),loader:'tsx'},bundle:true,write:false,format:'esm',platform:'browser',jsx:'automatic'});
const browser=await chromium.launch({headless:true});
try{
 const page=await browser.newPage();page.on('console',m=>{if(m.type()==='error')console.error('console:',m.text())});page.on('response',r=>{if(r.status()>=400)console.error('HTTP',r.status(),r.url())});page.on('pageerror',e=>console.error('browser:',e.message));let current='old',reads=0,polls=0,scanned=false;const counts=new Map<string,number>();
 await page.route('**/v1/**',async route=>{const url=new URL(route.request().url());const p=url.pathname;const json=(value:any)=>route.fulfill({json:value});
 if(p.endsWith('/events'))return route.abort();
 if(p.endsWith('/qr'))return route.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><rect width="10" height="10"/></svg>'});
 if(p==='/v1/bots/test/login-session')return json({id:'old',state:'expired',sequence:3,hasQr:false});
 if(p==='/v1/bots/test/login-sessions'){current='new-'+(++reads);return json({id:current})}
 if(p.startsWith('/v1/login-sessions/')){polls++;const sid=p.split('/')[3];const n=(counts.get(sid)||0)+1;counts.set(sid,n);return json({id:sid,state:scanned?'waiting_confirm':n>=3?'waiting_scan':'starting',sequence:n>=3?2:1,hasQr:n>=3})}
 return route.fulfill({status:404,json:{error:'unexpected'}});
 });
 await page.route('**/test.js',route=>route.fulfill({contentType:'text/javascript',body:bundle.outputFiles[0].text}));await page.route('**/qr-regression',route=>route.fulfill({contentType:'text/html',body:'<html><body><div id="root"></div><script type="module" src="/test.js"></script></body></html>'}));
 await page.goto((process.env.QR_TEST_URL||'http://127.0.0.1:5179')+'/qr-regression');
 for(let i=1;i<=2;i++){await page.getByRole('button',{name:'刷新 / 重新登录',exact:true}).click();const img=page.locator('img[src*="new-'+i+'/qr"]');await img.waitFor({timeout:10000});assert.match(await img.getAttribute('src')||'',new RegExp('new-'+i+'/qr'));await page.getByText('当前未能自动登录，请使用手机 QQ 扫码并确认登录。',{exact:true}).waitFor();}
 scanned=true;await page.getByText(/服务器已收到扫码回执/).waitFor({timeout:10000});assert.equal(await page.locator('img[src*="/qr"]').count(),0,'scanned QR is hidden to prevent repeated scans');
 page.once('dialog',dialog=>dialog.dismiss());const beforeRefresh=reads;await page.getByRole('button',{name:'刷新 / 重新登录',exact:true}).click();assert.equal(reads,beforeRefresh,'declining refresh preserves an in-progress scanned session');
 await page.goto('about:blank');const before=polls;await page.waitForTimeout(1500);assert.equal(polls,before,'unmount stops polling');console.log('PASS: repeated QR refresh without SSE or page reload; unmount stops polling');
}finally{await browser.close()}
