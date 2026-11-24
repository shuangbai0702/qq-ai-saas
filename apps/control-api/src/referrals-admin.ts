import type {FastifyInstance} from 'fastify';
import {z} from 'zod';
import type {Sql} from '../../../packages/database/index.js';
import {requireAdmin} from './http-guards.js';

export function registerReferralsAdmin(app:FastifyInstance,db:Sql){
 const paging=z.object({q:z.string().trim().max(100).default(''),page:z.coerce.number().int().min(1).max(10000).default(1)});
 const pageSize=20;
 app.get('/v1/admin/referrals',async req=>{
  requireAdmin(req);const {q,page}=paging.parse(req.query);
  const where="WHERE ($1='' OR strpos(lower(u.email),lower($1))>0 OR strpos(lower(u.name),lower($1))>0)";
  const [count]=await db.query('SELECT count(*)::int AS total FROM users u '+where,[q]);
  const items=await db.query(`SELECT u.id,u.name,u.email,u.created_at,rc.user_id IS NOT NULL AS generated,
   (SELECT count(*)::int FROM referral_rewards r WHERE r.inviter_id=u.id) AS invited,
   (SELECT max(r.rewarded_at) FROM referral_rewards r WHERE r.inviter_id=u.id) AS last_success_at,
   (SELECT count(*)::int FROM audit_events a WHERE a.event='referral.link_copied' AND a.detail->>'actorId'=u.id) AS copies,
   (SELECT max(a.created_at) FROM audit_events a WHERE a.event='referral.link_generated' AND a.detail->>'actorId'=u.id) AS last_generated_at,
   (SELECT max(a.created_at) FROM audit_events a WHERE a.event='referral.link_copied' AND a.detail->>'actorId'=u.id) AS last_copied_at
   FROM users u LEFT JOIN referral_codes rc ON rc.user_id=u.id ${where} ORDER BY u.created_at DESC,u.id LIMIT $2 OFFSET $3`,[q,pageSize,(page-1)*pageSize]);
  return{items,total:count.total,page,pageSize};
 });
 app.get<{Params:{id:string}}>('/v1/admin/referrals/:id',async req=>{
  requireAdmin(req);const id=z.uuid().parse(req.params.id),{page}=paging.parse(req.query);
  const [user]=await db.query('SELECT id,name,email FROM users WHERE id=$1',[id]);
  if(!user)throw Object.assign(new Error('用户不存在'),{statusCode:404});
  const [count]=await db.query('SELECT count(*)::int AS total FROM referral_rewards WHERE inviter_id=$1',[id]);
  const items=await db.query('SELECT u.id,u.name,u.email,r.rewarded_at FROM referral_rewards r JOIN users u ON u.id=r.invitee_id WHERE r.inviter_id=$1 ORDER BY r.rewarded_at DESC,u.id LIMIT $2 OFFSET $3',[id,pageSize,(page-1)*pageSize]);
  return{user,items,total:count.total,page,pageSize};
 });
}
