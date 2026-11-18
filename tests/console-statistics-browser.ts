import assert from 'node:assert/strict';
import {chromium} from 'playwright';
import {mkdir} from 'node:fs/promises';

const browser=await chromium.launch({headless:true});
try{
 const page=await browser.newPage({viewport:{width:1440,height:1000}});
 const counts={list:0,detail:0};const errors:string[]=[];
 page.on('pageerror',error=>errors.push(error.message));
 const bots=[{id:'12345678-1234-4123-8123-123456789012',name:'统计样例',qq:'100000001',workspace:'测试工作区',protocol:'napcat',online:true,aiReady:true}];
 await page.addInitScript(()=>localStorage.setItem('fishbot:announcement:stats-test:2026-11-19-qq-connection-and-referral','read'));
 await page.route('**/v1/**',async route=>{
  const url=new URL(route.request().url());let data:any;
  if(url.pathname==='/v1/auth/me')data={user:{id:'stats-test',name:'统计测试',is_admin:true}};
  else if(url.pathname==='/v1/admin/bots'){counts.list++;assert.equal(url.searchParams.get('realOnly'),'true');data={items:bots,total:1,pageSize:20}}
  else if(url.pathname.endsWith('/statistics')){counts.detail++;data={sampledAt:1795000000,rounds:{count:12,threshold:30,status:'available'},partial:false,requests:[{id:'request-a',at:1795000000,phase:'foreground',model:'test-model',status:'completed',wireCount:1,inputTokens:12540,cachedTokens:10000,textChars:23000,toolsChars:1800,imageCount:0},{id:'request-b',at:1794999999,phase:'inner_execution',model:'test-model',status:'failed',wireCount:1,inputTokens:null,cachedTokens:null,textChars:null,toolsChars:null,imageCount:null}]}}
  else throw Error('Unexpected API request: '+url.pathname);
  await route.fulfill({json:data});
 });
 await page.goto((process.env.WEB_URL||'http://127.0.0.1:23934')+'/#/announcements');
 await page.getByRole('heading',{name:'公告',exact:true}).waitFor();
 assert.deepEqual(counts,{list:0,detail:0});
 await page.evaluate(()=>location.hash='/admin/statistics');
 await page.getByRole('button',{name:/统计样例/}).waitFor();
 assert.deepEqual(counts,{list:1,detail:0});
 await page.getByRole('button',{name:/统计样例/}).click();
 await page.getByText('当前有效业务轮：12 / 30').waitFor();
 assert.equal(counts.detail,1);
 assert.ok(await page.getByText('12,540',{exact:true}).count());
 assert.ok(await page.getByText('未返回',{exact:true}).count());
 await page.waitForTimeout(5500);
 assert.deepEqual(counts,{list:1,detail:1},'No background polling');
 await page.getByRole('button',{name:'刷新统计',exact:true}).click();
 await page.waitForFunction(()=>!!document.querySelector('.stats-request'));
 await page.getByRole('button',{name:'收起',exact:true}).click();
 assert.equal(counts.detail,2);
 await page.getByRole('button',{name:/统计样例/}).click();
 await page.getByText('当前有效业务轮：12 / 30').waitFor();
 await mkdir('.local/console-statistics-20261119',{recursive:true});
 await page.screenshot({path:'.local/console-statistics-20261119/desktop.png',fullPage:true});
 await page.setViewportSize({width:390,height:844});
 assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'Mobile must not overflow');
 await page.screenshot({path:'.local/console-statistics-20261119/mobile.png',fullPage:true});
 await page.evaluate(()=>location.hash='/announcements');
 await page.getByRole('heading',{name:'公告',exact:true}).waitFor();
 const done={...counts};await page.waitForTimeout(300);assert.deepEqual(counts,done);
 assert.deepEqual(errors,[]);
 console.log('PASS: on-demand list/detail, manual refresh, no polling, exit, desktop/mobile; '+JSON.stringify(counts));
}finally{await browser.close()}
