import 'dotenv/config';import {openDatabase} from './index.js';
const db=await openDatabase(process.env.DATA_ROOT||'.local',process.env.DATABASE_URL);console.log('Migrations current:',(await db.query('SELECT version FROM schema_migrations ORDER BY version')).map(r=>r.version).join(', '));await db.close();
