import test from 'node:test';
import assert from 'node:assert/strict';
import {createHmac} from 'node:crypto';
import {mkdtemp} from 'node:fs/promises';
import path from 'node:path';
import {proxyClientIp} from '../apps/control-api/src/proxy-client.js';
import {createApp} from '../apps/control-api/src/app.js';

const secret='synthetic-edge-secret';
function signed(ip:string,at=Date.now()){
 return {'x-fishbot-client-ip':ip,'x-fishbot-client-at':String(at),'x-fishbot-client-signature':createHmac('sha256',secret).update(at+'\n'+ip).digest('hex')};
}
test('only a fresh edge signature supplies the rate-limit identity',()=>{
 const now=Date.now(),headers=signed('203.0.113.1',now);
 assert.equal(proxyClientIp(headers,secret,now),'203.0.113.1');
 assert.equal(proxyClientIp({...headers,'x-fishbot-client-ip':'203.0.113.2'},secret,now),undefined);
 assert.equal(proxyClientIp(headers,'different-secret',now),undefined);
 assert.equal(proxyClientIp(headers,undefined,now),undefined);
 assert.equal(proxyClientIp(headers,secret,now+90001),undefined);
 assert.equal(proxyClientIp(signed('not-an-ip',now),secret,now),undefined);
 assert.equal(proxyClientIp({'x-forwarded-for':'203.0.113.1'},secret,now),undefined);
});
test('one edge client cannot exhaust another client login quota',async t=>{
 const previous=process.env.FISHBOT_PROXY_SECRET;process.env.FISHBOT_PROXY_SECRET=secret;
 let app:Awaited<ReturnType<typeof createApp>>['app'];
 try{({app}=await createApp({root:await mkdtemp(path.resolve('.local/proxy-client-')),agent:false}))}
 finally{if(previous===undefined)delete process.env.FISHBOT_PROXY_SECRET;else process.env.FISHBOT_PROXY_SECRET=previous}
 t.after(()=>app.close());
 const login=(ip:string)=>app.inject({method:'POST',url:'/v1/auth/login',headers:signed(ip),payload:{email:'unknown@example.test',password:'synthetic-only'}});
 for(let n=0;n<15;n++)assert.equal((await login('203.0.113.1')).statusCode,401);
 assert.equal((await login('203.0.113.1')).statusCode,429);
 assert.equal((await login('203.0.113.2')).statusCode,401);
 assert.equal((await app.inject('/v1/health')).statusCode,200);
});
