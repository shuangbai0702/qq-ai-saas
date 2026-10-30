// Local maintenance helper. Credential arrives through hidden TTY input, never argv/files.
import {readFile,writeFile,mkdir} from 'node:fs/promises';
import {probeModel,hostedModel} from '../packages/shared/model-probe.ts';
if(!process.stdin.isTTY)throw new Error('A TTY is required for hidden input');
console.log('Paste API Key (hidden), then Enter. Ctrl+C cancels.');
process.stdin.setRawMode(true);process.stdin.resume();
let key=await new Promise((resolve,reject)=>{let value='';const read=chunk=>{for(const char of chunk.toString()){if(char==='\u0003'){process.stdin.off('data',read);reject(new Error('Canceled'));return}if(char==='\r'||char==='\n'){process.stdin.off('data',read);resolve(value.trim());return}if(char==='\u007f')value=value.slice(0,-1);else value+=char}};process.stdin.on('data',read)});
process.stdin.setRawMode(false);process.stdin.pause();if(!key)throw new Error('Empty credential');
const info=JSON.parse(await readFile('.local/real-qq-test.json','utf8'));const url='http://127.0.0.1:8787/v1/bots/'+info.botId;
const d=await(await fetch(url,{headers:{Cookie:info.cookie}})).json();const r=await fetch(url+'/config',{method:'PUT',headers:{Cookie:info.cookie,'Content-Type':'application/json'},body:JSON.stringify({expectedRevision:d.bot.revision,name:d.bot.name,expectedUin:d.bot.expected_uin,config:{...d.config,provider:'custom',...hostedModel},apiKey:key})});
if(!r.ok)throw new Error('Encrypted save failed with HTTP '+r.status);console.log('Credential saved through encrypted SaaS storage.');
const result=await probeModel(hostedModel.baseUrl,hostedModel.model,key,true);key='';await mkdir('test-results/round3',{recursive:true});await writeFile('test-results/round3/real-model-probe-'+Date.now()+'.json',JSON.stringify(result,null,2));console.log(JSON.stringify(result,null,2));
