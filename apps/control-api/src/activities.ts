import type {FastifyInstance} from 'fastify';
import type {Sql} from '../../../packages/database/index.js';
import {tokenPromotion} from '../../../packages/shared/promotion.js';
import {creditBalance} from './platform-credits.js';

export function registerActivities(app:FastifyInstance,db:Sql,enabled:(tx?:Sql)=>Promise<boolean>){
 async function details(tx:Sql,tenantId:string){
  const [clock]=await tx.query('SELECT clock_timestamp() AS now');
  const serverTime=new Date(clock.now).toISOString(),balance=await creditBalance(tx,tenantId);
  return {...tokenPromotion,serverTime,claimOpen:serverTime>=tokenPromotion.startsAt&&serverTime<tokenPromotion.endsAt,
   enabled:await enabled(tx),balance};
 }
 app.get('/v1/activities',async req=>details(db,req.user!.tenant_id));
 app.post('/v1/activities/claim',{config:{rateLimit:{max:10,timeWindow:'1 minute'}}},async req=>{
  const user=req.user!;
  return db.transaction(async tx=>{
   await tx.query('INSERT INTO platform_model_credits(tenant_id) VALUES($1) ON CONFLICT DO NOTHING',[user.tenant_id]);
   const [row]=await tx.query('SELECT campaign_id FROM platform_model_credits WHERE tenant_id=$1 FOR UPDATE',[user.tenant_id]);
   if(row.campaign_id===tokenPromotion.id)return {...await details(tx,user.tenant_id),alreadyClaimed:true};
   if(row.campaign_id)throw Object.assign(new Error('当前工作区已有其他活动额度'),{statusCode:409});
   const [claimed]=await tx.query(`WITH claim_clock AS MATERIALIZED (SELECT clock_timestamp() AS at)
    UPDATE platform_model_credits SET granted=granted+$2,campaign_id=$3,campaign_granted=$2,
    campaign_claimed_at=claim_clock.at,campaign_expires_at=claim_clock.at+interval '7 days'
    FROM claim_clock WHERE tenant_id=$1 AND claim_clock.at>=$4::timestamptz AND claim_clock.at<$5::timestamptz RETURNING tenant_id`,
    [user.tenant_id,tokenPromotion.tokens,tokenPromotion.id,tokenPromotion.startsAt,tokenPromotion.endsAt]);
   if(!claimed)throw Object.assign(new Error('活动尚未开始或领取时间已结束'),{statusCode:409});
   await tx.query("INSERT INTO audit_events(tenant_id,event,detail) VALUES($1,'activity.tokens_claimed',$2)",[user.tenant_id,JSON.stringify({actorId:user.id,campaignId:tokenPromotion.id,tokens:tokenPromotion.tokens})]);
   return {...await details(tx,user.tenant_id),alreadyClaimed:false};
  });
 });
}
