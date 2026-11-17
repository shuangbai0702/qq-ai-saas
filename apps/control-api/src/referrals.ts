import {randomBytes} from 'node:crypto';
import type {FastifyInstance} from 'fastify';
import {z} from 'zod';
import type {Sql} from '../../../packages/database/index.js';

const inviteeTokens=20_000_000;
const inviterTokens=30_000_000;
export const referralInput=z.string().regex(/^[A-Za-z0-9_-]{24}$/,'邀请链接无效').optional();

export async function grantReferral(tx:Sql, code:string, inviteeId:string, inviteeTenant:string){
 const [inviter]=await tx.query('SELECT user_id,tenant_id FROM referral_codes WHERE code=$1',[code]);
 if(!inviter||inviter.user_id===inviteeId||inviter.tenant_id===inviteeTenant)throw Object.assign(new Error('邀请链接无效'),{statusCode:400});
 const [reward]=await tx.query('INSERT INTO referral_rewards(invitee_id,inviter_id) VALUES($1,$2) ON CONFLICT DO NOTHING RETURNING invitee_id',[inviteeId,inviter.user_id]);
 if(!reward)return;
 await tx.query(`INSERT INTO platform_model_credits(tenant_id,granted) VALUES($1,$2),($3,$4)
   ON CONFLICT (tenant_id) DO UPDATE SET granted=platform_model_credits.granted+EXCLUDED.granted`,[inviter.tenant_id,inviterTokens,inviteeTenant,inviteeTokens]);
}

export async function creditBalance(db:Sql,tenantId:string){
 const [row]=await db.query('SELECT granted,spent,reserved FROM platform_model_credits WHERE tenant_id=$1',[tenantId]);
 const granted=Number(row?.granted??0),spent=Number(row?.spent??0),reserved=Number(row?.reserved??0);
 return {granted,spent,reserved,available:Math.max(0,granted-spent-reserved)};
}

export function registerReferrals(app:FastifyInstance,db:Sql,enabled:boolean){
 app.get('/v1/platform-model/balance',async req=>({enabled,...await creditBalance(db,req.user!.tenant_id)}));
 app.get('/v1/referrals',async req=>{
  const user=req.user!;
  const [code]=await db.query('SELECT code FROM referral_codes WHERE user_id=$1 AND tenant_id=$2',[user.id,user.tenant_id]);
  const [count]=await db.query('SELECT count(*) AS count FROM referral_rewards WHERE inviter_id=$1',[user.id]);
  return {enabled,code:code?.code??null,invited:Number(count.count),inviterTokens,inviteeTokens,...await creditBalance(db,user.tenant_id)};
 });
 app.post('/v1/referrals',{config:{rateLimit:{max:10,timeWindow:'1 minute'}}},async req=>{
  if(!enabled)throw Object.assign(new Error('邀请活动暂不可用'),{statusCode:503});
  const user=req.user!;
  await db.query('INSERT INTO referral_codes(user_id,tenant_id,code) VALUES($1,$2,$3) ON CONFLICT (user_id) DO NOTHING',[user.id,user.tenant_id,randomBytes(18).toString('base64url')]);
  const [row]=await db.query('SELECT code FROM referral_codes WHERE user_id=$1 AND tenant_id=$2',[user.id,user.tenant_id]);
  if(!row)throw Object.assign(new Error('当前工作区无法创建邀请链接'),{statusCode:403});
  return {code:row.code};
 });
}
