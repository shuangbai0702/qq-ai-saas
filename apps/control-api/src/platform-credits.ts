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
 const [row]=await tx.query(`SELECT ${columns} FROM platform_model_credits WHERE tenant_id=$1 FOR UPDATE`,[tenantId]);
 if(row?.campaign_expires_at){const [clock]=await tx.query('SELECT $1::timestamptz>clock_timestamp() AS active',[row.campaign_expires_at]);row.campaign_active=clock.active}
 const balance=balanceFromRow(row);
 if(!row||balance.available<tokens)throw Object.assign(new Error('平台模型余额不足以处理本次请求'),{statusCode:402});
 const campaignTokens=Math.min(tokens,balance.campaign.available),id=randomUUID();
 await tx.query('UPDATE platform_model_credits SET reserved=reserved+$2,campaign_reserved=campaign_reserved+$3 WHERE tenant_id=$1',[tenantId,tokens,campaignTokens]);
 await tx.query('INSERT INTO platform_model_reservations(id,tenant_id,bot_id,tokens,campaign_tokens) VALUES($1,$2,$3,$4,$5)',[id,tenantId,botId,tokens,campaignTokens]);
 return id;
}
export async function settleCredits(db:Sql,id:string,used:number){
 await db.transaction(async tx=>{
  const [row]=await tx.query('DELETE FROM platform_model_reservations WHERE id=$1 RETURNING tenant_id,tokens,campaign_tokens',[id]);
  if(row)await tx.query('UPDATE platform_model_credits SET reserved=reserved-$2,spent=spent+$3,campaign_reserved=campaign_reserved-$4,campaign_spent=campaign_spent+$5 WHERE tenant_id=$1',
   [row.tenant_id,row.tokens,used,row.campaign_tokens,Math.min(used,Number(row.campaign_tokens))]);
 });
}
