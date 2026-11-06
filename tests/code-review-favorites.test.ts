import test from 'node:test';
import assert from 'node:assert/strict';
import Fastify from 'fastify';
import {createHash} from 'node:crypto';
import {mkdtemp,mkdir,writeFile,readFile,rm} from 'node:fs/promises';
import path from 'node:path';
import {pathToFileURL} from 'node:url';

const source=path.resolve(process.env.REVIEW_SOURCE||'.');
const {registerQQFavorites}=await import(pathToFileURL(path.join(source,'apps/control-api/src/qq-favorites.ts')).href);
const {withFavoriteIndex,readFavoriteIndex,writeFavoriteIndex}=await import(pathToFileURL(path.join(source,'packages/shared/qq-favorite-index.ts')).href);
const uncertain={description:'无法确定',meaning:'',avoid:'',confidence:'uncertain',version:1,model:'fixture',at:'now',usage:null,costStatus:'unknown'};
const id='qqf_'+createHash('sha256').update('one').digest('hex').slice(0,16);
async function fixture(t:any,caption?:any,existing=false){
 const root=await mkdtemp(path.resolve('.local/code-review-favorite-')),app=Fastify(),bot={id:'a',generation:1,qq_uin:'90000',protocol:'napcat',desired_state:'running',health:{protocol:{online:true}}};
 const folder=path.join(root,'bots/a/qq-favorites');
 if(existing){await mkdir(path.join(folder,'images'),{recursive:true});await writeFile(path.join(folder,'images',id+'.png'),'fixture');await writeFavoriteIndex(root,'a',{account:'90000',enabled:true,items:[{id,file:id+'.png',mime:'image/png',animated:false,enabled:false,meaning:'',avoid:'',semanticAttempts:0}]})}
 registerQQFavorites(app,root,async()=>({...bot}),{call:async(_r:any,_b:any,action:string)=>action==='get_login_info'?{user_id:'90000'}:[{md5:'one',desc:'QQ原始描述不能代替视觉语义',url:'https://qpic.cn/fixture'}],download:async()=>Buffer.from('fixture'),normalize:async(_input:string,output:string)=>{await writeFile(output+'.png','fixture');return {suffix:'.png',mime:'image/png',animated:false}},caption});
 t.after(async()=>{await app.close();assert.ok(root.startsWith(path.resolve('.local')+path.sep));await rm(root,{recursive:true,force:true})});
 const url='/v1/bots/a/qq-favorites';
 async function sync(){assert.equal((await app.inject({url:url+'/sync',method:'POST'})).statusCode,202);for(let n=0;n<200;n++){const state=(await app.inject({url})).json();if(!state.syncing){assert.equal(state.error,undefined);return state}await new Promise(r=>setTimeout(r,5))}throw Error('offline sync did not finish')}
 return{root,app,url,sync};
}

test('web sync never turns QQ raw descriptions into automatic semantics',async t=>{
 const {sync}=await fixture(t);const state=await sync();assert.equal(state.items[0].meaning,'');assert.equal(state.items[0].enabled,false);assert.equal(state.items[0].semanticStatus,'raw_text');assert.match(state.items[0].rawText,/QQ/);
});
test('web resync shares the bounded uncertain attempt ledger',async t=>{
 let calls=0;const {sync,root}=await fixture(t,async()=>{calls++;return uncertain},true);
 for(let n=0;n<4;n++){await sync();await withFavoriteIndex(root,'a',async()=>{const index=await readFavoriteIndex(root,'a');index.items[0].semanticRetryAt=0;await writeFavoriteIndex(root,'a',index)})}
 const index=await readFavoriteIndex(root,'a');assert.equal(calls,3);assert.equal(index.items[0].semanticAttempts,3);assert.equal(index.items[0].semanticStatus,'uncertain');assert.equal(index.items[0].enabled,false);
});
test('web sync cannot overwrite concurrent maintenance retry state or invent manual consent',async t=>{
 let enter!:()=>void,release!:()=>void;const entered=new Promise<void>(r=>enter=r),resume=new Promise<void>(r=>release=r);
 const {root,sync}=await fixture(t,async()=>{enter();await resume;return uncertain},true);
 const pending=sync();await entered;
 try{await withFavoriteIndex(root,'a',async()=>{const index=await readFavoriteIndex(root,'a');Object.assign(index.items[0],{semantic:uncertain,semanticStatus:'uncertain',semanticAttempts:2});await writeFavoriteIndex(root,'a',index)})}finally{release()}
 await pending;const index=await readFavoriteIndex(root,'a');assert.equal(index.items[0].semanticAttempts,2);assert.equal(index.items[0].manual,undefined);assert.equal(index.items[0].meaning,'');
});
