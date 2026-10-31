import type {FastifyInstance} from 'fastify';import {z} from 'zod';
import type {Sql} from '../../../packages/database/index.js';import {runtimeStatus} from '../../../packages/shared/runtime-status.js';
export function registerAdminOperations(app:FastifyInstance,db:Sql){
 const fail=(message:string,statusCode=400):never=>{throw Object.assign(new Error(message),{statusCode})};
 function admin(req:any){if(!req.user?.is_admin)fail('需要管理员权限',403)}
 const query=z.object({q:z.string().trim().max(100).default(''),page:z.coerce.number().int().min(1).max(10000).default(1)});
 const pageSize=20;
 app.get('/v1/admin/workspaces',async req=>{admin(req);const {q,page}=query.parse(req.query);const args=[q,pageSize,(page-1)*pageSize];
 const where="WHERE ($1='' OR strpos(lower(t.name),lower($1))>0 OR EXISTS(SELECT 1 FROM memberships m JOIN users u ON u.id=m.user_id WHERE m.tenant_id=t.id AND (strpos(lower(u.email),lower($1))>0 OR strpos(lower(u.name),lower($1))>0)))";
 const [count]=await db.query('SELECT count(*)::int AS total FROM tenants t '+where,[q]);
 const items=await db.query(`SELECT t.id,t.name,t.bot_limit,t.created_at,(SELECT count(*)::int FROM bots b WHERE b.tenant_id=t.id AND b.deleted_at IS NULL) AS used,COALESCE((SELECT json_agg(json_build_object('id',u.id,'name',u.name,'email',u.email,'admin',u.is_admin) ORDER BY u.email) FROM memberships m JOIN users u ON u.id=m.user_id WHERE m.tenant_id=t.id),'[]'::json) AS members FROM tenants t ${where} ORDER BY t.created_at DESC,t.id LIMIT $2 OFFSET $3`,args);
 return{items,total:count.total,page,pageSize};
 });
 app.put<{Params:{id:string}}>('/v1/admin/workspaces/:id/quota',async req=>{admin(req);const id=z.uuid().parse(req.params.id);const v=z.object({limit:z.number().int().min(1).max(20),expectedLimit:z.number().int().min(1).max(20),reason:z.string().trim().min(2).max(200)}).strict().parse(req.body);
 return db.transaction(async tx=>{const [t]=await tx.query('SELECT bot_limit FROM tenants WHERE id=$1 FOR UPDATE',[id]);if(!t)fail('工作区不存在',404);if(t.bot_limit!==v.expectedLimit)fail('名额已被其他操作修改，请刷新后重试',409);
 const [usage]=await tx.query('SELECT count(*)::int AS used FROM bots WHERE tenant_id=$1 AND deleted_at IS NULL',[id]);if(v.limit<usage.used)fail('名额不能小于现有机器人数量（含删除中）',409);
 if(t.bot_limit===v.limit)return{changed:false,limit:v.limit,used:usage.used};
 await tx.query('UPDATE tenants SET bot_limit=$2 WHERE id=$1',[id,v.limit]);await tx.query("INSERT INTO audit_events(tenant_id,event,detail) VALUES($1,'admin.quota.updated',$2)",[id,JSON.stringify({actorId:req.user!.id,previousLimit:t.bot_limit,limit:v.limit,reason:v.reason})]);return{changed:true,limit:v.limit,used:usage.used};});
 });
 app.get('/v1/admin/bots',async req=>{admin(req);const {q,page}=query.parse(req.query);const filter=z.enum(['all','attention','online','stopped']).default('all').parse((req.query as any).filter);
 // Health freshness is identical to the public runtime status helper (90s / future 5s).
 const online="b.desired_state='running' AND b.health->'protocol'->>'online'='true' AND COALESCE(b.health->>'sampledAt','') >= to_char(timezone('UTC',now())-interval '90 seconds','YYYY-MM-DD\"T\"HH24:MI:SS.MS\"Z\"') AND COALESCE(b.health->>'sampledAt','') <= to_char(timezone('UTC',now())+interval '5 seconds','YYYY-MM-DD\"T\"HH24:MI:SS.MS\"Z\"')";
 const where=`WHERE b.deleted_at IS NULL AND ($1='' OR strpos(lower(b.name),lower($1))>0 OR strpos(COALESCE(q.qq_uin,b.expected_uin),$1)>0 OR strpos(lower(t.name),lower($1))>0 OR EXISTS(SELECT 1 FROM memberships m JOIN users u ON u.id=m.user_id WHERE m.tenant_id=t.id AND strpos(lower(u.email),lower($1))>0)) AND ($2='all' OR ($2='online' AND (${online})) OR ($2='stopped' AND b.desired_state IN ('stopped','offline')) OR ($2='attention' AND (b.last_error IS NOT NULL OR (b.desired_state='running' AND NOT COALESCE((${online}),false)))))`;
 const [count]=await db.query('SELECT count(*)::int AS total FROM bots b JOIN tenants t ON t.id=b.tenant_id LEFT JOIN qq_bindings q ON q.bot_id=b.id '+where,[q,filter]);
 const items=await db.query(`SELECT b.id,b.name,b.tenant_id,t.name AS workspace,b.protocol,b.runtime_mode,b.group_id,b.desired_state,b.observed_state,b.revision,b.applied_revision,b.last_error,b.health,COALESCE(q.qq_uin,NULLIF(b.expected_uin,'')) AS qq FROM bots b JOIN tenants t ON t.id=b.tenant_id LEFT JOIN qq_bindings q ON q.bot_id=b.id ${where} ORDER BY b.created_at DESC,b.id LIMIT $3 OFFSET $4`,[q,filter,pageSize,(page-1)*pageSize]);
 return{items:items.map(b=>{const r=runtimeStatus(b);return{id:b.id,name:b.name,tenantId:b.tenant_id,workspace:b.workspace,qq:b.qq,protocol:b.protocol,mode:b.runtime_mode,groupId:b.group_id,desired:b.desired_state,observed:b.observed_state,revision:b.revision,appliedRevision:b.applied_revision,hasError:!!b.last_error,online:b.desired_state==='running'&&r.online,fresh:r.fresh,aiReady:b.desired_state==='running'&&r.aiReady,sampledAt:b.health?.sampledAt||null}}),total:count.total,page,pageSize};
 });
 app.get('/v1/admin/operations',async req=>{admin(req);const {page}=query.parse(req.query);const [count]=await db.query("SELECT count(*)::int AS total FROM audit_events WHERE event='admin.quota.updated'");
 const items=await db.query("SELECT a.id::text,a.created_at,t.name AS workspace,a.detail->>'previousLimit' AS previous_limit,a.detail->>'limit' AS new_limit,a.detail->>'reason' AS reason,u.name AS actor FROM audit_events a LEFT JOIN tenants t ON t.id=a.tenant_id LEFT JOIN users u ON u.id=a.detail->>'actorId' WHERE a.event='admin.quota.updated' ORDER BY a.created_at DESC,a.id DESC LIMIT $1 OFFSET $2",[pageSize,(page-1)*pageSize]);return{items,total:count.total,page,pageSize};
 });
}
