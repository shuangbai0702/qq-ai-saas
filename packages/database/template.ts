// Optional deployment artifact: a closed, empty PGlite data directory. Never
// generated from a user's database. Preparation cost belongs to the build host.
import {PGlite} from '@electric-sql/pglite';
import {readFile,writeFile,mkdir,lstat,readdir,copyFile,rename,rm} from 'node:fs/promises';
import {createReadStream} from 'node:fs';import {createHash,randomUUID} from 'node:crypto';import path from 'node:path';import {fileURLToPath} from 'node:url';
const version=async()=>JSON.parse(await readFile(fileURLToPath(new URL('../package.json',import.meta.resolve('@electric-sql/pglite'))),'utf8')).version as string;
async function exists(file:string){try{await lstat(file);return true}catch(e:any){if(e.code==='ENOENT')return false;throw e}}
async function hash(file:string){const h=createHash('sha256');for await(const chunk of createReadStream(file))h.update(chunk);return h.digest('hex')}
async function cleanStage(stage:string,parent:string){if(path.dirname(stage)!==path.resolve(parent))throw new Error('Invalid template staging path');await rm(stage,{recursive:true,force:true})}
export async function preparePGliteTemplate(destination:string){
 const target=path.resolve(destination);if(await exists(target))throw new Error('Template destination already exists; use a new versioned directory');
 await mkdir(path.dirname(target),{recursive:true});const stage=target+'.prepare-'+randomUUID();await mkdir(stage);let db:PGlite|undefined;
 try{
  db=new PGlite(path.join(stage,'data'),{startParams:[...PGlite.defaultStartParams,'-c','shared_buffers=8MB','-c','work_mem=1MB','-c','maintenance_work_mem=8MB']});await db.waitReady;
  const result=await db.query<{count:number}>("SELECT count(*)::int AS count FROM pg_tables WHERE schemaname NOT IN ('pg_catalog','information_schema')");if(result.rows[0].count!==0)throw new Error('Template must have no application tables');await db.close();db=undefined;
  const files:Array<{path:string;sha256:string}>=[],directories:string[]=[];
  async function walk(dir:string){for(const item of await readdir(dir,{withFileTypes:true})){const file=path.join(dir,item.name),relative=path.relative(path.join(stage,'data'),file).split(path.sep).join('/');if(item.isSymbolicLink())throw new Error('Template symlink denied');if(item.isDirectory()){directories.push(relative);await walk(file)}else if(item.isFile())files.push({path:relative,sha256:await hash(file)});else throw new Error('Unsupported template entry')}}await walk(path.join(stage,'data'));
  await writeFile(path.join(stage,'manifest.json'),JSON.stringify({format:1,pgliteVersion:await version(),empty:true,files,directories},null,2),{mode:0o600});await rename(stage,target);return{files:files.length,pgliteVersion:await version()};
 }catch(e){if(db)await db.close().catch(()=>{});await cleanStage(stage,path.dirname(target));throw e}
}
export async function seedPGliteIfMissing(dataRoot:string,source=process.env.PGLITE_TEMPLATE_DIR||path.resolve('resources/pglite-template')){
 const parent=path.resolve(dataRoot),target=path.join(parent,'postgres');if(await exists(target))return false;
 const manifestFile=path.join(source,'manifest.json');if(!await exists(manifestFile))return false;
 const manifest=JSON.parse(await readFile(manifestFile,'utf8'));if(manifest.format!==1||manifest.empty!==true||manifest.pgliteVersion!==await version()||!Array.isArray(manifest.files)||manifest.files.length>5000||!Array.isArray(manifest.directories)||manifest.directories.length>5000)throw new Error('PGlite template version or manifest mismatch');
 const stage=path.join(parent,'postgres.seed-'+randomUUID());await mkdir(stage,{recursive:true});
 try{
  for(const dir of manifest.directories){if(typeof dir!=='string'||!dir||dir.includes('\\')||dir.includes(':')||path.posix.isAbsolute(dir)||dir.split('/').some((p:string)=>!p||p==='.'||p==='..'))throw new Error('Invalid template directory');await mkdir(path.join(stage,dir),{recursive:true})}
  const seen=new Set<string>();for(const row of manifest.files){if(typeof row.path!=='string'||!row.path||row.path.includes('\\')||row.path.includes(':')||path.posix.isAbsolute(row.path)||row.path.split('/').some((p:string)=>!p||p==='.'||p==='..')||seen.has(row.path)||! /^[a-f0-9]{64}$/.test(row.sha256))throw new Error('Invalid template entry');seen.add(row.path);
   const from=path.join(source,'data',row.path),to=path.join(stage,row.path);if(!(await lstat(from)).isFile()||(await lstat(from)).isSymbolicLink())throw new Error('Template file must be regular');await mkdir(path.dirname(to),{recursive:true});await copyFile(from,to);if(await hash(to)!==row.sha256)throw new Error('PGlite template checksum mismatch');
  }
  if(!seen.has('PG_VERSION'))throw new Error('Incomplete PGlite template');
  if(await exists(target))throw new Error('Database appeared during template preparation');await rename(stage,target);return true;
 }catch(e){await cleanStage(stage,parent);throw e}
}
