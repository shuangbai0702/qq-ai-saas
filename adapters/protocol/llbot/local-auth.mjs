import https from 'node:https';
import {readFile} from 'node:fs/promises';
import path from 'node:path';
import {timingSafeEqual} from 'node:crypto';
import {once} from 'node:events';
import {fileURLToPath} from 'node:url';

/** A private CONNECT endpoint, not a machine proxy or shared auth service. */
export async function localAuth(server,{manifest:m,spawnChild,getProcess,onFailure,onRequest=()=>{}}){
 const assets=path.join(m.runtimeDir,'bin/pmhq/auth-local');
 const [cert,key,body]=await Promise.all(['leaf.pem','leaf-key.pem','sign-info.json'].map(name=>readFile(path.join(assets,name))));
 const sockets=new Set();let closing=false,failed=false,probe,helper,flight,queue=[],buffer='';
 const token=Buffer.from(m.management.token+':local');
 const tlsServer=https.createServer({cert,key,handshakeTimeout:10000},(request,response)=>{
  onRequest({method:request.method,url:request.url});
  let url;try{url=new URL(request.url,'https://api-auth.luckylillia.com')}catch{response.writeHead(400);response.end();return}
  if(!['GET','POST'].includes(request.method)||url.origin!=='https://api-auth.luckylillia.com'||!/^\/api\/[a-zA-Z0-9/_-]+$/.test(url.pathname)){response.writeHead(404);response.end();return}
  const payload=url.pathname==='/api/sign/info'?body:Buffer.from('{}');request.resume();
  response.writeHead(200,{'Content-Type':'application/json','Content-Length':payload.length,'Cache-Control':'no-store'});response.end(payload);
 });
 tlsServer.on('tlsClientError',()=>{});
 function fail(){if(!closing&&!failed){failed=true;for(const entry of queue.splice(0))entry.reject(Error('Local bridge failed'));buffer='';onFailure();for(const socket of sockets)socket.destroy()}}
 async function patch(){
  if(closing||failed)throw Error('Local bridge closed');
  const pmhq=getProcess();if(!pmhq?.pid)throw Error('Owned PMHQ unavailable');
  if(!helper){
   // A child birth time is obtained in the same owner process, never by name.
   probe=spawnChild(m.python,['-c','import json,psutil,sys;print(json.dumps({"created":psutil.Process(int(sys.argv[1])).create_time(),"python":sys._base_executable,"paths":sys.path}))',String(pmhq.pid)],{windowsHide:true,stdio:['ignore','pipe','ignore']});
   let output='';probe.stdout.on('data',b=>{output+=b});
   const exited=once(probe,'exit');const timer=setTimeout(()=>probe.kill(),5000);
   let proof;
   try{const [code]=await exited;proof=JSON.parse(output);if(code!==0||closing||failed||!Number.isFinite(proof.created)||typeof proof.python!=='string'||!path.isAbsolute(proof.python)||!Array.isArray(proof.paths)||proof.paths.some(p=>typeof p!=='string'))throw Error('PMHQ birth proof unavailable')}finally{clearTimeout(timer);probe=undefined}
   // Preserve the venv's modules without its resident redirector process/job.
   // This is the same base-interpreter handoff used by protocol_process.py.
   const bootstrap=`import runpy,sys;sys.path[:]=${JSON.stringify(proof.paths)};sys.argv=sys.argv[1:];runpy.run_path(sys.argv[0],run_name='__main__')`;
   helper=spawnChild(proof.python,['-B','-c',bootstrap,path.join(path.dirname(fileURLToPath(import.meta.url)),'local-auth.py'),JSON.stringify({pid:pmhq.pid,created:proof.created,parent:process.pid,executable:path.join(m.runtimeDir,'bin/pmhq/pmhq.exe'),assets})],{windowsHide:true,stdio:['pipe','pipe','ignore']});
   helper.on('error',fail);helper.on('exit',()=>{for(const entry of queue.splice(0))entry.reject(Error('Local bridge exited'));fail()});
   helper.stdin.on('error',fail);
   helper.stdout.on('data',raw=>{if(closing||failed)return;buffer+=raw.toString();if(buffer.length>16384){fail();return}for(;;){const end=buffer.indexOf('\n');if(end<0)break;const line=buffer.slice(0,end);buffer=buffer.slice(end+1);const entry=queue.shift();if(!entry)continue;try{JSON.parse(line).ok===true?entry.resolve():entry.reject(Error('Local bridge not ready'))}catch{entry.reject(Error('Invalid local bridge response'))}}});
  }
  return new Promise((resolve,reject)=>{const entry={resolve,reject};queue.push(entry);const timer=setTimeout(()=>{reject(Error('Local bridge deadline'));fail()},8000);entry.resolve=()=>{clearTimeout(timer);resolve()};entry.reject=e=>{clearTimeout(timer);reject(e)};helper.stdin.write(JSON.stringify({op:'patch'})+'\n')});
 }
 const connect=(request,socket,head)=>{
  const supplied=Buffer.from(/^Basic\s+([A-Za-z0-9+/]+=*)$/i.exec(request.headers['proxy-authorization']||'')?.[1]||'','base64');
  if(closing||failed||request.url!=='api-auth.luckylillia.com:443'||supplied.length!==token.length||!timingSafeEqual(supplied,token)||sockets.size>=8){socket.destroy();return}
  sockets.add(socket);socket.on('error',()=>{});socket.on('close',()=>sockets.delete(socket));socket.setTimeout(20000,()=>socket.destroy());socket.pause();
  flight??=patch().finally(()=>{flight=undefined});
  void flight.then(()=>{if(closing||socket.destroyed)return;socket.write('HTTP/1.1 200 Connection Established\r\n\r\n');if(head.length)socket.unshift(head);tlsServer.emit('connection',socket);socket.resume()}).catch(()=>{socket.destroy();fail()});
 };
 server.on('connect',connect);
 return{proxy:`http://${m.management.token}:local@127.0.0.1:${m.management.port}`,close(){if(closing)return;closing=true;server.off('connect',connect);for(const socket of sockets)socket.destroy();for(const entry of queue.splice(0))entry.reject(Error('Local bridge closed'));probe?.kill();helper?.stdin.end();setTimeout(()=>helper?.kill(),1000).unref();tlsServer.close()}};
}
