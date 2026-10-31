import {mkdir,writeFile,access} from 'node:fs/promises';import path from 'node:path';import {randomBytes} from 'node:crypto';
const root=path.resolve(process.argv[2]||process.cwd()),email=process.argv[3]||'admin@example.com',origin=process.argv[4]||'http://127.0.0.1:8787';
if(!/^[^\s"'=]+@[^\s"'=]+\.[^\s"'=]+$/.test(email))throw new Error('Provide an administrator email');const url=new URL(origin);if(!['http:','https:'].includes(url.protocol)||url.origin!==origin)throw new Error('APP_ORIGIN must be an exact HTTP(S) origin');
const env=path.join(root,'.env.docker'),secrets=path.join(root,'.local/docker-secrets'),passwordFile=path.join(secrets,'admin-password'),postgresPasswordFile=path.join(secrets,'postgres-password');
const exists=async file=>{try{await access(file);return true}catch(e){if(e.code==='ENOENT')return false;throw e}};const hasEnv=await exists(env),hasPassword=await exists(passwordFile),hasPostgresPassword=await exists(postgresPasswordFile);if(hasEnv||hasPassword||hasPostgresPassword){if(!hasEnv||!hasPassword||!hasPostgresPassword)throw new Error('Incomplete deployment configuration: preserve existing files and restore the missing counterpart');console.log('Existing deployment configuration and passwords preserved');process.exit(0)}
await mkdir(secrets,{recursive:true,mode:0o700});
// Readable by the unprivileged container through the secret mount. The host
// parent remains 0700, and neither path enters the image build context.
await writeFile(passwordFile,randomBytes(24).toString('base64url')+'\n',{mode:0o444,flag:'wx'});
await writeFile(postgresPasswordFile,randomBytes(32).toString('base64url')+'\n',{mode:0o444,flag:'wx'});
await writeFile(env,`FISHBOT_IMAGE=fishbot/qq-ai-saas:0.1.0\nAPP_ORIGIN=${origin}\nPORT=8787\nBIND_ADDRESS=127.0.0.1\nBOOTSTRAP_ADMIN_EMAIL=${email}\nCOOKIE_SECURE=${url.protocol==='https:'}\n`,{mode:0o600,flag:'wx'});
console.log('Deployment configuration created. Initial password is in .local/docker-secrets/admin-password; secrets are never printed or built into the image.');
