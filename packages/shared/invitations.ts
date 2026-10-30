import {randomBytes,randomUUID} from 'node:crypto';import type {Sql} from '../database/index.js';import {digest} from './security.js';
export async function issueInvitation(db:Sql,creator:string|null,options:{note?:string;maxUses?:number;expiresInDays?:number;botLimit?:number}={}){
 const code='FISH-'+randomBytes(18).toString('base64url'),id=randomUUID();
 const [invitation]=await db.query('INSERT INTO invitations(id,code_hash,code_prefix,created_by,note,max_uses,expires_at,bot_limit) VALUES($1,$2,$3,$4,$5,$6,$7,$8) RETURNING id,code_prefix,note,max_uses,used_count,bot_limit,expires_at,created_at',[id,digest(code),code.slice(0,10),creator,options.note||'',options.maxUses??1,new Date(Date.now()+(options.expiresInDays??7)*86400000),options.botLimit??1]);
 return{code,invitation};
}
