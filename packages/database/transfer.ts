import {createHash} from 'node:crypto';

export const transferSchema={
 users:['id','email','password_hash','name','is_admin','created_at'],
 tenants:['id','name','created_at','bot_limit'],
 memberships:['user_id','tenant_id','role'],
 sessions:['id','user_id','expires_at'],
 runtime_groups:['id','node_id','capacity','desired_state','observed_state','generation','health_port','token','health','created_at'],
 bots:['id','tenant_id','name','protocol','expected_uin','desired_state','observed_state','generation','revision','applied_revision','health','last_error','deleted_at','created_at','runtime_mode','group_id'],
 bot_config_revisions:['bot_id','revision','config','created_at'],
 secrets:['id','tenant_id','bot_id','ciphertext','updated_at'],
 instances:['bot_id','node_id','generation','ai_port','protocol_port','token','heartbeat_at','health'],
 qq_bindings:['bot_id','qq_uin','bound_at','binding_kind'],
 login_sessions:['id','bot_id','generation','state','sequence','qr','qq_uin','message','expires_at','created_at'],
 commands:['id','bot_id','idempotency_key','generation','kind','status','error','created_at','finished_at'],
 invitations:['id','code_hash','code_prefix','created_by','note','max_uses','used_count','expires_at','revoked_at','created_at','bot_limit'],
 invitation_redemptions:['invitation_id','user_id','redeemed_at'],
 group_members:['bot_id','group_id','desired_state','observed_state','generation','applied_revision','joined_at'],
 audit_events:['id','tenant_id','event','bot_id','detail','created_at'],
 autonomy_settings:['bot_id','tenant_id','epoch','runtime_epoch','config','state','goal_revision','summary','wait','observation','last_error','last_event_at','lease_owner','lease_until','next_wake_at','updated_at','context_cycle_seq','context_checkpoint'],
 autonomy_goals:['id','bot_id','tenant_id','origin','status','title','reason','progress','success_condition','source','revision','created_at','updated_at'],
 autonomy_cycles:['work_expires_at','id','bot_id','tenant_id','epoch','generation','config_revision','owner','state','budget','usage','reason','decision','started_at','finished_at','cycle_seq'],
 autonomy_wakes:['bot_id','tenant_id','dedupe_key','epoch','due_at','reason','scope','created_at'],
 autonomy_daily_usage:['bot_id','tenant_id','day','reserved_tokens','used_tokens','model_calls','tool_calls','external_actions'],
 bilibili_settings:['bot_id','tenant_id','revision','config','model_secret','updated_at'],
 bilibili_accounts:['bot_id','tenant_id','uid','label','secret','revision','state','refresh_state','pending_confirm','updated_at'],
 bilibili_logins:['bot_id','tenant_id','id','secret','state','expires_at','updated_at'],
 tool_effects:['cycle_id','id','tenant_id','bot_id','work_id','step_id','operation','args','args_digest','authority','sender_id','mode_epoch','generation','config_revision','settings_revision','account_revision','account_uid','state','lease_owner','lease_until','cancel_requested','deadline_at','result','created_at','updated_at','origin_scope','origin_event','continuation','completion_state','completion_owner','completion_until','usage_checkpoint','budget_reserved'],
 tool_service_cooldowns:['service','until_at','reason'],
 tool_cancellations:['bot_id','tenant_id','work_id','step_id','expires_at'],
} as const;
export type TransferTable=keyof typeof transferSchema;
export const transferTables=Object.keys(transferSchema) as TransferTable[];
export const expectedSchemaVersions=['001','002','003','004','005','006','007','008','009','010','011','012','013','014'];

function canonical(value:any):any{
 if(value instanceof Date)return value.toISOString();
 if(typeof value==='bigint')return value.toString();
 if(Array.isArray(value))return value.map(canonical);
 if(value&&typeof value==='object')return Object.fromEntries(Object.keys(value).sort().map(k=>[k,canonical(value[k])]));
 return value;
}
export function contentSha256(data:Record<TransferTable,{rows:any[]}>){
 // ORDER BY the first column leaves composite-key ties unordered across engines.
 // Hash the full row multiset so ordering is irrelevant and duplicates still count.
 const content=transferTables.map(table=>[table,data[table].rows.map(row=>canonical(table==='audit_events'?{...row,id:String(row.id)}:row)).sort((a,b)=>{const left=JSON.stringify(a),right=JSON.stringify(b);return left<right?-1:left>right?1:0})]);
 return createHash('sha256').update(JSON.stringify(content)).digest('hex');
}
export function validateTransfer(data:any){
 if(!data||typeof data!=='object')throw new Error('Malformed snapshot tables');
 for(const table of transferTables){
  const rows=data[table]?.rows;
  if(!Array.isArray(rows))throw new Error(`Malformed snapshot table ${table}`);
  for(const row of rows){
   if(!row||typeof row!=='object'||Array.isArray(row)||Object.keys(row).length!==transferSchema[table].length||Object.keys(row).some(k=>!(transferSchema[table] as readonly string[]).includes(k))||transferSchema[table].some(k=>!Object.hasOwn(row,k)))throw new Error(`Invalid row columns in ${table}`);
  }
 }
 if(Object.keys(data).length!==transferTables.length||Object.keys(data).some(k=>!transferTables.includes(k as TransferTable)))throw new Error('Unexpected snapshot tables');
}
