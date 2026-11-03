import test from 'node:test';
import assert from 'node:assert/strict';
import {execFile} from 'node:child_process';
import path from 'node:path';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {BotConfigSchema,defaultConfig} from '../packages/contracts/index.js';
import {MemoryControls} from '../apps/web/src/memory-controls.js';

test('independent embedding address is visible and changes preserve Bot behavior',()=>{
 const config=BotConfigSchema.parse({...defaultConfig,behavior:{...defaultConfig.behavior,embeddingBaseUrl:'http://localhost:11434/v1'}});
 const view=MemoryControls({config,onChange:updated=>{assert.equal(updated.behavior?.embeddingBaseUrl,'https://embedding.example/v1');assert.equal(updated.behavior?.embeddingModel,config.behavior?.embeddingModel);}});
 const markup=renderToStaticMarkup(view);
 assert.match(markup,/http:\/\/localhost:11434\/v1/);
 assert.match(markup,/allowlist/);
 assert.match(markup,/1000/);
 assert.match(markup,/同一机器人按真实QQ ID在群聊和私聊互通/);
 assert.match(markup,/不同机器人、租户及人物身份保持隔离/);
 let changed=false;
 function visit(node:unknown){
  if(!React.isValidElement(node))return;
  const props=node.props as {children?:React.ReactNode;'aria-label'?:string;onChange?:(event:unknown)=>void};
  if(props['aria-label']==='Embedding 服务地址'){props.onChange?.({target:{value:'https://embedding.example/v1'}});changed=true;}
  React.Children.forEach(props.children,visit);
 }
 visit(view);assert.equal(changed,true);
});

test('bounded summary and personal fact semantics, independent SDK and late deletion fences',{timeout:30000},async()=>{
 const output=await new Promise<string>((resolve,reject)=>execFile(path.resolve(`references/astrbot/.venv/${process.platform==='win32'?'Scripts/python.exe':'bin/python'}`),['-B','tests/long_memory_test.py'],{windowsHide:true,timeout:25000},(error,stdout,stderr)=>error?reject(new Error(`${error}\n${stdout}\n${stderr}`)):resolve(stdout+stderr)));
 assert.match(output,/Ran 17 tests/);assert.match(output,/OK/);
});
