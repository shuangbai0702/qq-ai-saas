import {issueTestInvite} from './helpers/invitations.js';
import test from 'node:test';import assert from 'node:assert/strict';import {mkdtemp} from 'node:fs/promises';import path from 'node:path';import {createApp} from '../apps/control-api/src/app.js';
test('new bots share the controlled runtime by default while dedicated remains available',async t=>{
 const {app,db}=await createApp({root:await mkdtemp(path.resolve('.local/default-runtime-')),agent:false});t.after(()=>app.close());
 const r=await app.inject({method:'POST',url:'/v1/auth/register',payload:{inviteCode:await issueTestInvite(db,2),email:'runtime-default@test.invalid',name:'默认模式',password:'runtime-default-123'}});const cookies={fish_session:r.cookies[0].value};
 for(const mode of [undefined,'dedicated']){const created=await app.inject({method:'POST',url:'/v1/bots',cookies,payload:{name:'模式测试',...(mode?{runtimeMode:mode}:{})}});assert.equal(created.statusCode,201);const detail=await app.inject({method:'GET',url:`/v1/bots/${created.json().id}`,cookies});assert.equal(detail.json().bot.runtime_mode,mode||'shared');}
});
test('persisted shared capacity overflow and ownership of runtime mutations',async t=>{
 const {app,db,agent}=await createApp({root:await mkdtemp(path.resolve('.local/group-capacity-')),agent:false});t.after(()=>app.close());const r=await app.inject({method:'POST',url:'/v1/auth/register',payload:{inviteCode:await issueTestInvite(db,9),email:'capacity@test.invalid',name:'容量',password:'capacity-pass-123'}});const cookies={fish_session:r.cookies[0].value};const ids=[];
 for(let i=0;i<9;i++){const created=await app.inject({method:'POST',url:'/v1/bots',cookies,payload:{name:'C'+i,runtimeMode:'shared'}});ids.push(created.json().id);await agent.shared.assign(created.json().id);}
 const groups=await db.query('SELECT * FROM runtime_groups');assert.equal(groups.length,2);assert.ok(groups.every(g=>g.capacity===8));const sizes=await db.query('SELECT group_id,count(*)::int AS n FROM group_members GROUP BY group_id');assert.deepEqual(sizes.map(x=>x.n).sort(),[1,8]);
 // An older empty/stopped group must not draw a new member away from a live group.
 const live=groups.find(g=>sizes.some(x=>x.group_id===g.id&&x.n===1))!;
 await db.query("UPDATE runtime_groups SET desired_state='running' WHERE id=$1",[live.id]);
 await db.query("INSERT INTO runtime_groups(id,capacity,health_port,token,created_at) VALUES('group-empty-old',8,62001,'fixture','2020-01-01')");
 const moved=ids[0];await db.query('DELETE FROM group_members WHERE bot_id=$1',[moved]);await db.query('UPDATE bots SET group_id=NULL WHERE id=$1',[moved]);
 assert.equal((await agent.shared.assign(moved)).id,live.id,'reuse the running shared group before stopped historical groups');
 assert.equal((await agent.shared.assign(moved)).id,live.id,'assignment is idempotent');
 const stale=await app.inject({method:'PUT',url:`/v1/bots/${ids[0]}/runtime`,cookies,payload:{mode:'dedicated',expectedRevision:0}});assert.equal(stale.statusCode,409);
 await db.query("UPDATE bots SET desired_state='running' WHERE id=$1",[ids[0]]);assert.equal((await app.inject({method:'PUT',url:`/v1/bots/${ids[0]}/runtime`,cookies,payload:{mode:'dedicated',expectedRevision:1}})).statusCode,409);
 const other=await app.inject({method:'POST',url:'/v1/auth/register',payload:{inviteCode:await issueTestInvite(db,1),email:'other@test.invalid',name:'隔离',password:'capacity-pass-123'}});assert.equal((await app.inject({method:'PUT',url:`/v1/bots/${ids[0]}/runtime`,cookies:{fish_session:other.cookies[0].value},payload:{mode:'dedicated',expectedRevision:1}})).statusCode,404);
});
