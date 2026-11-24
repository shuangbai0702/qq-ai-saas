import test from 'node:test';
import assert from 'node:assert/strict';
import {BotConfigSchema,defaultConfig} from '../packages/contracts/index.js';
test('semantic memory defaults enabled and remote requests stay bounded',()=>{
 assert.equal(defaultConfig.behavior?.memorySemantic,true);
 for(const behavior of [{dailyEmbeddingLimit:1001},{embeddingModel:'x'.repeat(151)}])assert.equal(BotConfigSchema.safeParse({...defaultConfig,behavior:{...defaultConfig.behavior,...behavior}}).success,false);
 const parsed=BotConfigSchema.parse({...defaultConfig,behavior:{...defaultConfig.behavior,memorySemantic:true,embeddingModel:' BAAI/bge-m3 '}});
 assert.equal(parsed.behavior?.embeddingModel,'BAAI/bge-m3');
});
