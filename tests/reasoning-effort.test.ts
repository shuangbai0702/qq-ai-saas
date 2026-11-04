import test from 'node:test';import assert from 'node:assert/strict';
import {mkdtemp,readFile} from 'node:fs/promises';import path from 'node:path';
import {execFile} from 'node:child_process';import {promisify} from 'node:util';
import {AstrBotAdapter} from '../adapters/astrbot/index.js';
import {BotConfigSchema,defaultConfig} from '../packages/contracts/index.js';

test('optional per-bot reasoning effort reaches the native SDK without changing defaults',{timeout:30000},async()=>{
 assert.equal(BotConfigSchema.parse(defaultConfig).reasoningEffort,undefined);
 assert.equal(BotConfigSchema.safeParse({...defaultConfig,reasoningEffort:'arbitrary'}).success,false);
 const root=await mkdtemp(path.resolve('.local/reasoning-effort-'));
 const config=BotConfigSchema.parse({...defaultConfig,reasoningEffort:'max'});
 const adapter=new AstrBotAdapter({inspect:async()=>null} as any,'','');
 await adapter.applyConfig({root,config,apiKey:'fixture',generation:1,revision:1,token:'fixture',botId:'fixture',tenantId:'fixture',aiPort:1234} as any);
 const file=path.join(root,'ai/data/cmd_config.json');
 assert.deepEqual(JSON.parse(await readFile(file,'utf8')).provider[0].custom_extra_body,{reasoning_effort:'max'});
 const python=path.resolve(`references/astrbot/.venv/${process.platform==='win32'?'Scripts/python.exe':'bin/python'}`);
 const result=await promisify(execFile)(python,['-X','utf8','-B','tests/reasoning_effort_test.py',file],{windowsHide:true,timeout:25000});
 assert.match(result.stdout,/native reasoning_effort=max serialized/);
});
