import assert from 'node:assert/strict';
import test from 'node:test';
import {defaultConfig,personas} from '../packages/contracts/index.js';
import {quoteReplyPolicy} from '../packages/contracts/fish-persona.js';

test('all default role System prompts include the quote preference once',()=>{
 for(const persona of personas)assert.equal(persona.prompt.split(quoteReplyPolicy).length-1,1,persona.id);
 assert.equal(defaultConfig.systemPrompt.split(quoteReplyPolicy).length-1,1);
});
