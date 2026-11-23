import {randomUUID} from 'node:crypto';
import type {Sql} from '../../../packages/database/index.js';

// Expiry is derived at read/reserve time. Existing totals remain the one ledger;
// reservations retain their funding source so a late release cannot revive a grant.
export function balanceFromRow(row:any){
 const granted=Number(row?.granted??0),spent=Number(row?.spent??0),reserved=Number(row?.reserved??0);
 const campaignRemaining=Math.max(0,Number(row?.campaign_granted??0)-Number(row?.campaign_spent??0)-Number(row?.campaign_reserved??0));
 const expired=row?.campaign_active===false?campaignRemaining:0;
 return {granted,spent,reserved,expired,available:Math.max(0,granted-spent-reserved-expired),
  campaign:{id:row?.campaign_id??null,claimedAt:row?.campaign_claimed_at??null,expiresAt:row?.campaign_expires_at??null,
   granted:Number(row?.campaign_granted??0),spent:Number(row?.campaign_spent??0),reserved:Number(row?.campaign_reserved??0),available:expired?0:campaignRemaining}};
}
const columns='*,campaign_expires_at>clock_timestamp() AS campaign_active';
export async function creditBalance(db:Sql,tenantId:string){
 const [row]=await db.query(`SELECT ${columns} FROM platform_model_credits WHERE tenant_id=$1`,[tenantId]);
 return balanceFromRow(row);
}
export async function reserveCredits(tx:Sql,tenantId:string,botId:string,tokens:number){
 if(!Number.isSafeInteger(tokens)||tokens<=0)throw new Error('invalid_credit_reservation');
 const [row]=await tx.query(`SELECT ${columns} FROM platform_model_credits WHERE tenant_id=$1 FOR UPDATE`,[tenantId]);
 if(row?.campaign_expires_at){const [clock]=await tx.query('SELECT $1::timestamptz>clock_timestamp() AS active',[row.campaign_expires_at]);row.campaign_active=clock.active}
 const balance=balanceFromRow(row);
 if(!row||balance.available<tokens)throw Object.assign(new Error('平台模型余额不足以处理本次请求'),{statusCode:402});
 const campaignTokens=Math.min(tokens,balance.campaign.available),id=randomUUID();
 await tx.query('UPDATE platform_model_credits SET reserved=reserved+$2,campaign_reserved=campaign_reserved+$3 WHERE tenant_id=$1',[tenantId,tokens,campaignTokens]);
 await tx.query("INSERT INTO platform_model_reservations(id,tenant_id,bot_id,tokens,campaign_tokens,dispatch_state) VALUES($1,$2,$3,$4,$5,'reserved')",[id,tenantId,botId,tokens,campaignTokens]);
 return id;
}
export async function settleCredits(db:Sql,id:string,used:number){
 if(!Number.isSafeInteger(used)||used<0)throw new Error('invalid_credit_usage');
 await db.transaction(tx=>settleTransaction(tx,id,used));
}
async function settleTransaction(tx:Sql,id:string,used:number){
  const [row]=await tx.query('DELETE FROM platform_model_reservations WHERE id=$1 AND (usage_tokens IS NULL OR usage_tokens=$2) RETURNING tenant_id,tokens,campaign_tokens',[id,used]);
  if(!row){const pending=await tx.query('SELECT id FROM platform_model_reservations WHERE id=$1',[id]);if(pending.length)throw new Error('credit_receipt_conflict')}
  if(row)await tx.query('UPDATE platform_model_credits SET reserved=reserved-$2,spent=spent+$3,campaign_reserved=campaign_reserved-$4,campaign_spent=campaign_spent+$5 WHERE tenant_id=$1',
   [row.tenant_id,row.tokens,used,row.campaign_tokens,Math.min(used,Number(row.campaign_tokens))]);
}

// Write before dispatch, then persist the actual receipt before touching balances.
// A crash between either pair of writes retains an explicit, recoverable outcome.
export async function dispatchCredits(db:Sql,id:string,providerId:string){
 const rows=await db.query("UPDATE platform_model_reservations SET dispatch_state='dispatched',provider_id=$2,dispatched_at=clock_timestamp(),diagnostic_code=NULL WHERE id=$1 AND dispatch_state='reserved' RETURNING id",[id,providerId]);
 if(!rows.length)throw new Error('credit_dispatch_conflict');
}
export async function receiptCredits(db:Sql,id:string,used:number){
 if(!Number.isSafeInteger(used)||used<0)throw new Error('invalid_credit_usage');
 const rows=await db.query("UPDATE platform_model_reservations SET dispatch_state='receipt',usage_tokens=$2,diagnostic_code=NULL WHERE id=$1 AND dispatch_state IN ('dispatched','receipt') AND (usage_tokens IS NULL OR usage_tokens=$2) RETURNING id",[id,used]);
 if(!rows.length){const pending=await db.query('SELECT id FROM platform_model_reservations WHERE id=$1',[id]);if(pending.length)throw new Error('credit_receipt_conflict')}
}
export async function unconfirmedCredits(db:Sql,id:string,code:string){
 if(!['transport_unconfirmed','usage_missing'].includes(code))throw new Error('invalid_credit_diagnostic');
 await db.query("UPDATE platform_model_reservations SET dispatch_state='unknown',diagnostic_code=$2 WHERE id=$1 AND dispatch_state='dispatched'",[id,code]);
}
export async function rejectedCredits(db:Sql,id:string){
 const rows=await db.query("UPDATE platform_model_reservations SET dispatch_state='reserved',diagnostic_code='upstream_rejected' WHERE id=$1 AND dispatch_state='dispatched' RETURNING id",[id]);
 if(!rows.length)throw new Error('credit_rejection_conflict');
}
export async function releaseUndispatchedCredits(db:Sql,id:string){
 // Only the request owner calls this after its dispatch path has exited.
 // Locking serializes a concurrent dispatch; unknown rows are never released.
 return db.transaction(async tx=>{
  const [row]=await tx.query('SELECT dispatch_state FROM platform_model_reservations WHERE id=$1 FOR UPDATE',[id]);
  if(row?.dispatch_state!=='reserved')return false;
  await settleTransaction(tx,id,0);return true;
 });
}
export async function recoverCreditReceipts(db:Sql){
 // Only receipts are recoverable without consulting a live request owner.
 // A 'reserved' row may still belong to an in-flight request; never reap it by age.
 return db.transaction(async tx=>{
  const rows=await tx.query("SELECT id,usage_tokens FROM platform_model_reservations WHERE dispatch_state='receipt' ORDER BY created_at FOR UPDATE");
  for(const row of rows)await settleTransaction(tx,row.id,Number(row.usage_tokens));
  return rows.length;
 });
}

// An explicit operator decision absorbs an unprovable legacy cost. This is
// not a zero-usage receipt. Keep the complete original reservation in the
// existing audit trail before releasing its hold, in the same transaction.
export async function waiveLegacyReservation(db:Sql,id:string,expected:{tokens:number;createdAt:string},actorId:string,reason:string){
 const conflict=()=>Object.assign(new Error('历史预留状态已变化，请刷新后重试'),{statusCode:409});
 if(!Number.isSafeInteger(expected.tokens)||expected.tokens<=0||!Number.isFinite(Date.parse(expected.createdAt))||!actorId||reason.trim().length<10||reason.length>240)throw new Error('invalid_credit_waiver');
 const matches=(row:any)=>Number(row.tokens)===expected.tokens&&new Date(row.created_at).getTime()===Date.parse(expected.createdAt);
 return db.transaction(async tx=>{
  const [row]=await tx.query('SELECT * FROM platform_model_reservations WHERE id=$1 FOR UPDATE',[id]);
  if(!row){
   const [audit]=await tx.query("SELECT detail FROM audit_events WHERE event='admin.platform_reservation.waived' AND detail->>'reservationId'=$1 LIMIT 1",[id]);
   if(audit){if(!matches(audit.detail.original))throw conflict();return {released:false,alreadyResolved:true,tokens:Number(audit.detail.original.tokens)}}
   throw Object.assign(new Error('预留不存在'),{statusCode:404});
  }
  if(!matches(row)||row.dispatch_state!=='unknown'||row.provider_id!==null||row.dispatched_at!==null||row.usage_tokens!==null)throw conflict();
  const changed=await tx.query('UPDATE platform_model_credits SET reserved=reserved-$2,campaign_reserved=campaign_reserved-$3 WHERE tenant_id=$1 AND reserved>=$2 AND campaign_reserved>=$3 RETURNING tenant_id',[row.tenant_id,row.tokens,row.campaign_tokens]);
  if(changed.length!==1)throw new Error('credit_balance_inconsistent');
  await tx.query("INSERT INTO audit_events(tenant_id,event,detail) VALUES($1,'admin.platform_reservation.waived',$2)",[row.tenant_id,JSON.stringify({actorId,reservationId:id,decision:'platform_absorbs_unknown',consumption:'unknown',reason:reason.trim(),original:row})]);
  await tx.query('DELETE FROM platform_model_reservations WHERE id=$1',[id]);
  return {released:true,alreadyResolved:false,tokens:Number(row.tokens)};
 });
}

// Explicit operator resolution for aged post-019 reservations ('unknown',
// 'dispatched', or 'reserved') whose owner process/request has exited without a
// receipt. Keeps waiveLegacyReservation strict for pre-019 rows while preventing
// permanent tenant balance lockups.
export async function resolveStaleReservation(
 db:Sql,
 id:string,
 expected:{state:'unknown'|'dispatched'|'reserved';tokens:number;createdAt:string;providerId:string|null},
 actorId:string,
 reason:string,
 minAgeMs=15*60*1000
){
 const conflict=()=>Object.assign(new Error('预留状态已变化或尚未达到可人工处置时长，请刷新后重试'),{statusCode:409});
 if(!Number.isSafeInteger(expected.tokens)||expected.tokens<=0||!Number.isFinite(Date.parse(expected.createdAt))||!actorId||reason.trim().length<10||reason.length>240)throw Object.assign(new Error('invalid_credit_resolution'),{statusCode:400});
 const matches=(row:any)=>row.dispatch_state===expected.state&&Number(row.tokens)===expected.tokens&&new Date(row.created_at).getTime()===Date.parse(expected.createdAt)&&(row.provider_id??null)===(expected.providerId??null);
 return db.transaction(async tx=>{
  const [row]=await tx.query('SELECT * FROM platform_model_reservations WHERE id=$1 FOR UPDATE',[id]);
  if(!row){
   const [audit]=await tx.query("SELECT detail FROM audit_events WHERE event='admin.platform_reservation.resolved' AND detail->>'reservationId'=$1 LIMIT 1",[id]);
   if(audit){if(!matches(audit.detail.original))throw conflict();return {released:false,alreadyResolved:true,tokens:Number(audit.detail.original.tokens)}}
   throw Object.assign(new Error('预留不存在'),{statusCode:404});
  }
  if(!matches(row)||row.usage_tokens!==null||Date.now()-new Date(row.created_at).getTime()<minAgeMs)throw conflict();
  const changed=await tx.query('UPDATE platform_model_credits SET reserved=reserved-$2,campaign_reserved=campaign_reserved-$3 WHERE tenant_id=$1 AND reserved>=$2 AND campaign_reserved>=$3 RETURNING tenant_id',[row.tenant_id,row.tokens,row.campaign_tokens]);
  if(changed.length!==1)throw new Error('credit_balance_inconsistent');
  await tx.query("INSERT INTO audit_events(tenant_id,event,detail) VALUES($1,'admin.platform_reservation.resolved',$2)",[row.tenant_id,JSON.stringify({actorId,reservationId:id,decision:'operator_resolved_stale_reservation',consumption:'unknown',reason:reason.trim(),original:row})]);
  await tx.query('DELETE FROM platform_model_reservations WHERE id=$1',[id]);
  return {released:true,alreadyResolved:false,tokens:Number(row.tokens)};
 });
}
