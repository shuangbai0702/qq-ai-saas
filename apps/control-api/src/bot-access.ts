import type {Sql} from '../../../packages/database/index.js';

export type OwnedBot=(req:any,id:string,tx:Sql)=>Promise<any>;

/** The caller owns the transaction and its business-specific 404/409 policy.
 * Re-read ownership after waiting for the lifecycle lock, never return a stale bot. */
export async function lockOwnedBot(tx:Sql,owned:OwnedBot,req:any,id:string){
 const owner=await owned(req,id,tx);
 await tx.query('SELECT id FROM bots WHERE id=$1 FOR UPDATE',[owner.id]);
 return owned(req,id,tx);
}

/** Revoke login observations, not the saved QQ protocol session.
 * Account replacement also revokes online observations; ordinary lifecycle
 * operations intentionally leave them intact. Terminal history is retained. */
export async function revokeLoginSessions(tx:Sql,target:{botId:string}|{sessionId:string},options:{includeOnline?:boolean;message?:string}={}){
 const byBot='botId' in target;
 const values:unknown[]=[byBot?target.botId:target.sessionId];
 const message=options.message===undefined?'':',message=$2';
 if(options.message!==undefined)values.push(options.message);
 const states=options.includeOnline?"'expired','canceled','failed'":"'online','expired','canceled','failed'";
 await tx.query(`UPDATE login_sessions SET state='canceled',qr=NULL,sequence=sequence+1${message} WHERE ${byBot?'bot_id':'id'}=$1 AND state NOT IN (${states})`,values);
}
