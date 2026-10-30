import {spawn} from 'node:child_process';import {mkdir,writeFile,readFile,rm} from 'node:fs/promises';
await mkdir('.local',{recursive:true});
const executable=process.execPath;
const api=spawn(executable,['--import','tsx','apps/control-api/src/main.ts'],{stdio:['inherit','inherit','inherit','ipc'],windowsHide:true});
const web=spawn(executable,['node_modules/vite/bin/vite.js','--config','apps/web/vite.config.ts'],{stdio:'inherit',windowsHide:true});
await writeFile('.local/dev-processes.json',JSON.stringify({launcher:process.pid,api:api.pid,web:web.pid}));
console.log('\n鱼栖开发入口：http://127.0.0.1:5173 （API http://127.0.0.1:8787）\n');
let closing=false;function close(){if(closing)return;closing=true;clearInterval(stopTimer);if(api.connected)api.send('shutdown');else api.kill('SIGTERM');web.kill('SIGTERM');}
const stopTimer=setInterval(async()=>{try{const pid=Number(await readFile('.local/dev.stop','utf8'));if(pid===process.pid){await rm('.local/dev.stop');close()}}catch{}},500);
process.on('SIGINT',close);process.on('SIGTERM',close);api.on('exit',close);web.on('exit',close);
