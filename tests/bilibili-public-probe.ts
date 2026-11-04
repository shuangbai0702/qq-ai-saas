// Anonymous, single-request diagnostic. Never authenticates or writes.
import {readFileSync} from 'node:fs';
import {BiliWeb,BiliError} from '../apps/node-agent/src/bilibili/web-adapter.js';
const read=(p:string)=>{try{return readFileSync(p,'utf8')}catch{return null}};
const before={memory:process.memoryUsage(),usage:process.resourceUsage(),host:read('/proc/meminfo'),psi:read('/proc/pressure/memory')};
const started=performance.now();let outcome:any;
try{const result=await new BiliWeb().get('/x/web-interface/popular',{pn:1,ps:2});outcome={status:'read_success',items:result.list?.length??0}}catch(e){outcome={status:'unavailable',code:e instanceof BiliError?e.code:'unexpected_error'}}
console.log(JSON.stringify({kind:'anonymous-public-read',outcome,elapsedMs:performance.now()-started,before,after:{memory:process.memoryUsage(),usage:process.resourceUsage(),smaps:read('/proc/self/smaps_rollup'),host:read('/proc/meminfo'),psi:read('/proc/pressure/memory')},writes:0,accountLogins:0,modelCalls:0},null,2));
