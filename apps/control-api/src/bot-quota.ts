import type {Sql} from '../../../packages/database/index.js';

// Called inside the create transaction. The tenant row serializes competing
// creates in PostgreSQL as well as the embedded database.
export async function getBotQuota(db:Sql,tenantId:string,lock=false){
 const [tenant]=await db.query('SELECT bot_limit FROM tenants WHERE id=$1'+(lock?' FOR UPDATE':''),[tenantId]);
 if(!tenant)throw Object.assign(new Error('工作区不存在'),{statusCode:404});
 const [row]=await db.query('SELECT count(*)::int AS used FROM bots WHERE tenant_id=$1 AND deleted_at IS NULL',[tenantId]);
 return {limit:tenant.bot_limit,used:row.used,available:Math.max(0,tenant.bot_limit-row.used),canCreate:row.used<tenant.bot_limit};
}
