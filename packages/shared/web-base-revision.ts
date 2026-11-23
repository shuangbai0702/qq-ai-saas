import {isDeepStrictEqual} from 'node:util';
import type {Sql} from '../database/index.js';

export const fixedRequestConfig=(config:any)=>Object.fromEntries(Object.entries(config).filter(([k])=>!['systemPrompt','toolPrompts','characterCards'].includes(k)));

/** Existing revision/audit rows prove a text-only web edit, never a new permission. */
export async function allowsInFlightBaseRevision(db:Sql,botId:string,tenantId:string,from:number,to:number){
 if(!Number.isSafeInteger(from)||!Number.isSafeInteger(to)||from<1||to<=from)return false;
 const rows=await db.query(`SELECT r.revision,r.config,a.detail FROM bot_config_revisions r
 LEFT JOIN LATERAL (SELECT detail FROM audit_events WHERE bot_id=r.bot_id AND tenant_id=$2
 AND event='config.saved' AND detail->>'revision'=r.revision::text ORDER BY id DESC LIMIT 1) a ON true
 WHERE r.bot_id=$1 AND r.revision BETWEEN $3 AND $4 ORDER BY r.revision`,[botId,tenantId,from,to]);
 if(rows.length!==to-from+1||rows[0]?.revision!==from)return false;
 for(let i=1;i<rows.length;i++){
  const prior=rows[i-1],next=rows[i],audit=next.detail;
  if(next.revision!==prior.revision+1||audit?.source!=='authenticated_web_config'||typeof audit.actorId!=='string'||!audit.actorId||audit.credentialChanged!==false
   ||!isDeepStrictEqual(fixedRequestConfig(prior.config),fixedRequestConfig(next.config)))return false;
 }
 return true;
}
