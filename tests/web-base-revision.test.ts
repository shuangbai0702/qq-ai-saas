import test from 'node:test';
import assert from 'node:assert/strict';
import {allowsInFlightBaseRevision} from '../packages/shared/web-base-revision.js';
const prior={systemPrompt:'old',toolPrompts:{dsh:'old'},delegatedCapabilities:{webSearch:true},model:'model'};
const audit={source:'authenticated_web_config',actorId:'real-user',credentialChanged:false};
const db=(config:any,detail:any=audit,rows?:any[])=>({query:async()=>rows||[{revision:2,config:prior},{revision:3,config,detail}]}) as any;
test('only audited text edits preserve an existing request revision',async()=>{
 assert(await allowsInFlightBaseRevision(db({...prior,systemPrompt:'new',toolPrompts:{dsh:'new'}}),'bot','tenant',2,3));
 assert(await allowsInFlightBaseRevision(db(prior),'bot','tenant',2,3));
 for(const config of [{...prior,model:'other'},{...prior,delegatedCapabilities:{webSearch:false}}])assert(!await allowsInFlightBaseRevision(db(config),'bot','tenant',2,3));
 for(const detail of [{...audit,credentialChanged:true},{...audit,actorId:''},{...audit,source:'model'},undefined])assert(!await allowsInFlightBaseRevision(db({...prior,systemPrompt:'new'},detail===undefined?null:detail),'bot','tenant',2,3));
 assert(!await allowsInFlightBaseRevision(db(prior,audit,[]),'bot','tenant',2,3));
 assert(!await allowsInFlightBaseRevision(db(prior),'bot','tenant',3,2));
});
