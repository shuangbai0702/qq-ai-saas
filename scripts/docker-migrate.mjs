import {openDatabase,configuredDatabaseUrl} from '../dist-server/packages/database/index.js';
process.env.DB_AUTO_MIGRATE='true';
const url=await configuredDatabaseUrl();if(!url)throw new Error('Explicit migration requires PostgreSQL');
const db=await openDatabase(process.env.DATA_ROOT||'/var/lib/fishbot',url);try{console.log('Schema migration complete')}finally{await db.close()}
