import {readFile,access} from 'node:fs/promises';import path from 'node:path';import {randomUUID} from 'node:crypto';import {pathToFileURL} from 'node:url';
import {openDatabase,configuredDatabaseUrl} from '../dist-server/packages/database/index.js';import {hashPassword} from '../dist-server/packages/shared/security.js';
export async function bootstrapAdmin(){
 const root=path.resolve(process.env.DATA_ROOT||'.local'),email=process.env.BOOTSTRAP_ADMIN_EMAIL?.trim().toLowerCase();
 if(!email||!/^\S+@\S+\.\S+$/.test(email))throw new Error('Valid BOOTSTRAP_ADMIN_EMAIL is required');
 const password=process.env.BOOTSTRAP_ADMIN_PASSWORD_FILE?(await readFile(process.env.BOOTSTRAP_ADMIN_PASSWORD_FILE,'utf8')).trimEnd():process.env.BOOTSTRAP_ADMIN_PASSWORD;
 if(!password||password.length<12||password.length>128)throw new Error('Bootstrap administrator password must have 12–128 characters');
 const db=await openDatabase(root,await configuredDatabaseUrl());
 try{return await db.transaction(async tx=>{
  const [user]=await tx.query('SELECT id,is_admin FROM users WHERE email=$1',[email]);
  if(user){if(!user.is_admin)throw new Error('Email belongs to an ordinary user; refusing automatic privilege escalation');console.log('Existing administrator preserved');return}
  const [admin]=await tx.query('SELECT id FROM users WHERE is_admin=true LIMIT 1');if(admin)throw new Error('Administrator already exists; refusing to create a second bootstrap account');
  const id=randomUUID(),tenant=randomUUID();await tx.query('INSERT INTO users(id,email,password_hash,name,is_admin) VALUES($1,$2,$3,$4,true)',[id,email,await hashPassword(password),'平台管理员']);await tx.query('INSERT INTO tenants(id,name) VALUES($1,$2)',[tenant,'管理员工作区']);await tx.query('INSERT INTO memberships(user_id,tenant_id) VALUES($1,$2)',[id,tenant]);console.log('Administrator initialized');
 })}finally{await db.close()}
}
if(process.argv[1]&&pathToFileURL(path.resolve(process.argv[1])).href===import.meta.url)await bootstrapAdmin();
