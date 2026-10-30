import test,{before} from 'node:test';import assert from 'node:assert/strict';import {mkdtemp,readFile,writeFile,mkdir,readdir,cp} from 'node:fs/promises';import path from 'node:path';
import {seedPGliteIfMissing,preparePGliteTemplate} from '../packages/database/template.js';import {openDatabase} from '../packages/database/index.js';import {loadKey} from '../packages/shared/security.js';
let fixture:string;
before(async()=>{fixture=path.join(await mkdtemp(path.resolve('.local/template-fixture-')),'empty');await preparePGliteTemplate(fixture)});
test('empty database template creates independent durable stores and never replaces existing data',{timeout:30000},async()=>{
 const root=await mkdtemp(path.resolve('.local/template-test-')),source=fixture;
 const a=path.join(root,'a'),b=path.join(root,'b');await mkdir(a);await mkdir(b);
 assert.equal(await seedPGliteIfMissing(a,source),true);const first=await openDatabase(a);await first.query('CREATE TABLE isolation_probe(value text)');await first.query('INSERT INTO isolation_probe VALUES($1)',['private-A']);await first.close();
 assert.equal(await seedPGliteIfMissing(a,path.join(root,'missing')),false);const reopened=await openDatabase(a);assert.equal((await reopened.query('SELECT * FROM isolation_probe'))[0].value,'private-A');await reopened.close();
 await seedPGliteIfMissing(b,source);const second=await openDatabase(b);assert.equal((await second.query("SELECT to_regclass('public.isolation_probe') AS value"))[0].value,null);await second.close();assert.notDeepEqual(await loadKey(a),await loadKey(b));
 const partial=path.join(root,'partial');await mkdir(path.join(partial,'postgres'),{recursive:true});await writeFile(path.join(partial,'postgres','sentinel'),'preserve');assert.equal(await seedPGliteIfMissing(partial,source),false);assert.equal(await readFile(path.join(partial,'postgres','sentinel'),'utf8'),'preserve');
});
test('template rejects corruption, version mismatch and traversal with no committed database',{timeout:30000},async()=>{
 const root=await mkdtemp(path.resolve('.local/template-invalid-')),source=path.join(root,'source'),destination=path.join(root,'target');await mkdir(destination);await cp(fixture,source,{recursive:true});const manifestFile=path.join(source,'manifest.json'),text=await readFile(manifestFile,'utf8'),manifest=JSON.parse(text);const pg=path.join(source,'data','PG_VERSION'),original=await readFile(pg);
 await writeFile(pg,'broken');await assert.rejects(()=>seedPGliteIfMissing(destination,source),/checksum/);assert.deepEqual(await readdir(destination),[]);await writeFile(pg,original);
 await writeFile(manifestFile,JSON.stringify({...manifest,pgliteVersion:'incompatible'}));await assert.rejects(()=>seedPGliteIfMissing(destination,source),/version/);
 await writeFile(manifestFile,JSON.stringify({...manifest,directories:['../../escape']}));await assert.rejects(()=>seedPGliteIfMissing(destination,source),/directory/);assert.deepEqual(await readdir(destination),[]);
 await writeFile(manifestFile,text);assert.equal(await seedPGliteIfMissing(destination,path.join(root,'missing')),false);
});
