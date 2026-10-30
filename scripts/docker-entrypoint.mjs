import {bootstrapAdmin} from './bootstrap-admin.mjs';
if(process.env.BOOTSTRAP_ADMIN_EMAIL)await bootstrapAdmin();
delete process.env.BOOTSTRAP_ADMIN_PASSWORD;delete process.env.BOOTSTRAP_ADMIN_PASSWORD_FILE;
await import('./production-start.mjs');
