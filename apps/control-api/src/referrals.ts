import {creditBalance} from './platform-credits.js';
export {creditBalance} from './platform-credits.js';
import {randomBytes} from 'node:crypto';
import type {FastifyInstance} from 'fastify';
import {z} from 'zod';
import type {Sql} from '../../../packages/database/index.js';
import {decrypt,digest,encrypt} from '../../../packages/shared/security.js';
import {registerReferralsAdmin} from './referrals-admin.js';

const inviteeTokens=20_000_000;
const inviterTokens=30_000_000;
export const referralInput=z.string().regex(/^[A-Za-z0-9_-]{24}$/,'邀请链接无效').optional();
const activationScope=(code:string)=>`referral-activation:${code}`;
const eligibility=`EXISTS(SELECT 1 FROM invitation_redemptions WHERE user_id=$1)
 AND NOT EXISTS(SELECT 1 FROM referral_rewards WHERE invitee_id=$1)`;

async function canInvite(db:Sql,userId:string){
 const [row]=await db.query(`SELECT ${eligibility} AS allowed`,[userId]);
 return row.allowed===true;
}

export function referralInvitationId(key:Buffer,code:string,activation:string){
 try{
  const id=decrypt(key,activationScope(code),activation);
  if(z.uuid().safeParse(id).success)return id;
 }catch{/* A mismatched or legacy activation is not a referral credential. */}
 throw Object.assign(new Error('邀请链接无效，请让对方重新生成'),{statusCode:400});
}

export async function grantReferral(tx:Sql, code:string, inviteeId:string, inviteeTenant:string){
 const [inviter]=await tx.query('SELECT user_id,tenant_id FROM referral_codes WHERE code=$1',[code]);
 if(!inviter||inviter.user_id===inviteeId||inviter.tenant_id===inviteeTenant)throw Object.assign(new Error('邀请链接无效'),{statusCode:400});
 if(!await canInvite(tx,inviter.user_id))throw Object.assign(new Error('此邀请链接已失效'),{statusCode:403});
 const [reward]=await tx.query('INSERT INTO referral_rewards(invitee_id,inviter_id) VALUES($1,$2) ON CONFLICT DO NOTHING RETURNING invitee_id',[inviteeId,inviter.user_id]);
 if(!reward)return;
 await tx.query(`INSERT INTO platform_model_credits(tenant_id,granted) VALUES($1,$2),($3,$4)
   ON CONFLICT (tenant_id) DO UPDATE SET granted=platform_model_credits.granted+EXCLUDED.granted`,[inviter.tenant_id,inviterTokens,inviteeTenant,inviteeTokens]);
}

export function registerReferrals(app:FastifyInstance,db:Sql,enabled:()=>Promise<boolean>,key:Buffer){
 registerReferralsAdmin(app,db);
 app.post('/v1/referrals/copied',{config:{rateLimit:{max:20,timeWindow:'1 minute'}}},async req=>{
  const {code}=z.object({code:referralInput.unwrap()}).strict().parse(req.body),user=req.user!;
  if(!(await db.query('SELECT user_id FROM referral_codes WHERE user_id=$1 AND tenant_id=$2 AND code=$3',[user.id,user.tenant_id,code])).length)throw Object.assign(new Error('邀请链接不属于当前账号'),{statusCode:403});
  await db.query("INSERT INTO audit_events(tenant_id,event,detail) VALUES($1,'referral.link_copied',$2)",[user.tenant_id,JSON.stringify({actorId:user.id})]);
  return{ok:true};
 });
 app.get('/v1/platform-model/balance',async req=>({enabled:await enabled(),...await creditBalance(db,req.user!.tenant_id)}));
 app.get('/v1/referrals',async req=>{
  const user=req.user!;
  const eligible=await canInvite(db,user.id);
  const [code]=eligible?await db.query('SELECT code FROM referral_codes WHERE user_id=$1 AND tenant_id=$2',[user.id,user.tenant_id]):[];
  const [count]=await db.query('SELECT count(*) AS count FROM referral_rewards WHERE inviter_id=$1',[user.id]);
  return {enabled:await enabled(),canInvite:eligible,code:code?.code??null,invited:Number(count.count),inviterTokens,inviteeTokens,...await creditBalance(db,user.tenant_id)};
 });
 app.post('/v1/referrals',{config:{rateLimit:{max:10,timeWindow:'1 minute'}}},async req=>{
  if(!await enabled())throw Object.assign(new Error('邀请活动暂不可用'),{statusCode:503});
  const user=req.user!;
  if(!await canInvite(db,user.id))throw Object.assign(new Error('仅使用管理员直接发放的激活码注册的账号可邀请好友'),{statusCode:403});
  const {inviteCode}=z.object({inviteCode:z.string().trim().min(1,'请输入激活码').max(100)}).parse(req.body);
  const [invitation]=await db.query('SELECT id FROM invitations WHERE code_hash=$1 AND revoked_at IS NULL AND expires_at>now() AND used_count<max_uses',[digest(inviteCode)]);
  if(!invitation)throw Object.assign(new Error('激活码无效、已过期或已用完'),{statusCode:400});
  const row=await db.transaction(async tx=>{
   await tx.query('INSERT INTO referral_codes(user_id,tenant_id,code) VALUES($1,$2,$3) ON CONFLICT (user_id) DO NOTHING',[user.id,user.tenant_id,randomBytes(18).toString('base64url')]);
   const [row]=await tx.query('SELECT code FROM referral_codes WHERE user_id=$1 AND tenant_id=$2',[user.id,user.tenant_id]);
   if(row)await tx.query("INSERT INTO audit_events(tenant_id,event,detail) VALUES($1,'referral.link_generated',$2)",[user.tenant_id,JSON.stringify({actorId:user.id,invitationId:invitation.id})]);
   return row;
  });
  if(!row)throw Object.assign(new Error('当前工作区无法创建邀请链接'),{statusCode:403});
  return {code:row.code,activation:encrypt(key,activationScope(row.code),invitation.id)};
 });
}
