import test from 'node:test';
import assert from 'node:assert/strict';
import Fastify from 'fastify';
import {registerConsoleStatistics} from '../apps/control-api/src/console-statistics.js';

test('statistics enforce admin and valid existing bot before accessing local data',async t=>{
 const app=Fastify();t.after(()=>app.close());
 let queries=0;
 app.decorateRequest('user',undefined);
 app.addHook('preHandler',async req=>{if(req.headers.authorization==='admin')req.user={is_admin:true} as any});
 registerConsoleStatistics(app,{query:async()=>{queries++;return []}} as any,'NOT-ACCESSED');
 assert.equal((await app.inject('/v1/admin/bots/12345678-1234-4123-8123-123456789012/statistics')).statusCode,403);
 assert.equal(queries,0);
 assert.equal((await app.inject({url:'/v1/admin/bots/12345678-1234-4123-8123-123456789012/statistics',headers:{authorization:'admin'}})).statusCode,404);
 assert.equal(queries,1);
 // A traversal is not accepted as a filesystem identity.
 assert.equal((await app.inject({url:'/v1/admin/bots/not-a-uuid/statistics',headers:{authorization:'admin'}})).statusCode,400);
 assert.equal(queries,1);
});
