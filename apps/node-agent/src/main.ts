import 'dotenv/config';import path from 'node:path';import {openDatabase} from '../../../packages/database/index.js';import {loadKey} from '../../../packages/shared/security.js';import {NodeAgent} from './agent.js';
if(!process.env.DATABASE_URL)throw new Error('独立Agent需要DATABASE_URL；嵌入式开发模式由API内置Agent统一管理数据库');
const root=path.resolve(process.env.DATA_ROOT||'.local');const db=await openDatabase(root,process.env.DATABASE_URL);let key:Buffer;try{key=await loadKey(root,{createIfMissing:!(await db.query('SELECT 1 FROM secrets LIMIT 1')).length})}catch(error){await db.close();throw error}const agent=new NodeAgent(db,root,key);await agent.begin();
for(const signal of ['SIGINT','SIGTERM'])process.on(signal,async()=>{await agent.shutdown();await db.close();process.exit()});
