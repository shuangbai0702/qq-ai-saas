import {randomUUID} from 'node:crypto';
import {WebSocket} from 'ws';

/** OneBot transport only. Pending requests belong to their exact source socket. */
export class LLBotRelay {
 constructor({expectedUin='',url,token,Socket=WebSocket,onIdentity=()=>{}}){Object.assign(this,{expectedUin,url,token,Socket,onIdentity});this.pending=new Map();this.closed=false;this.lastActivity=Date.now()}
 get online(){return !!this.upstream&&!!this.uin}
 get active(){return this.pending.size}
 attach(socket,claimedUin){
  if(this.closed||!/^\d{5,20}$/.test(claimedUin)||this.expectedUin&&this.expectedUin!==claimedUin){socket.close(1008);return}
  this.disconnectUpstream();this.upstream=socket;this.uin='';const events=[];
  socket.on('error',()=>{});
  socket.on('close',()=>{if(this.upstream===socket)this.disconnectUpstream()});
  socket.on('message',raw=>{
   if(this.upstream!==socket||this.closed)return;let data;try{data=JSON.parse(raw.toString())}catch{return}
   if(!data||typeof data!=='object'||Array.isArray(data))return;
   if(data.post_type!=='meta_event')this.lastActivity=Date.now();
   if(Object.hasOwn(data,'echo')){
    const pending=this.pending.get(data.echo);if(!pending||pending.upstream!==socket)return;
    this.pending.delete(data.echo);clearTimeout(pending.timer);pending.resolve(data);return;
   }
   if(!this.uin){if(events.length<16)events.push(data);else socket.close(1008);return}
   if(String(data.self_id||'')!==this.uin)return;
   if(this.downstream?.readyState===this.Socket.OPEN)this.downstream.send(JSON.stringify(data));
  });
  void this.call('get_login_info',{},4000,true).then(async result=>{
   if(this.upstream!==socket||this.closed)return;
   const uin=String(result.user_id||'');if(uin!==claimedUin||this.expectedUin&&uin!==this.expectedUin)throw Error('QQ identity mismatch');
   await this.onIdentity(uin);if(this.upstream!==socket||this.closed)return;
   this.uin=uin;this.expectedUin=uin;this.connectAI();
   // Only the initial bounded transport events, never fetched/replayed history.
   this.initialEvents=events.filter(event=>String(event.self_id||'')===uin);
  }).catch(()=>{if(this.upstream===socket)socket.close(1008)});
 }
 connectAI(){
  clearTimeout(this.reconnect);if(this.closed||!this.online||this.downstream)return;
  const socket=new this.Socket(this.url,{maxPayload:16*1024*1024,handshakeTimeout:4000,perMessageDeflate:false,headers:{Authorization:'Bearer '+this.token,'X-Self-ID':this.uin,'X-Client-Role':'Universal'}});this.downstream=socket;
  socket.on('error',()=>{});
  socket.on('open',()=>{if(this.downstream!==socket||this.closed){socket.close();return}for(const event of this.initialEvents||[])socket.send(JSON.stringify(event));this.initialEvents=undefined});
  socket.on('message',raw=>{
   if(this.downstream!==socket||this.closed)return;let data;try{data=JSON.parse(raw.toString())}catch{return}
   if(!data||typeof data!=='object'||Array.isArray(data))return;
   if(typeof data.action!=='string')return;
   void this.dispatch(data.action,data.params??{},data.action==='fetch_custom_face_detail'?600000:60000).then(result=>{
    if(this.downstream===socket&&socket.readyState===this.Socket.OPEN)socket.send(JSON.stringify({...result,echo:data.echo}));
   }).catch(()=>{if(this.downstream===socket&&socket.readyState===this.Socket.OPEN)socket.send(JSON.stringify({status:'failed',retcode:1504,data:null,echo:data.echo,message:'QQ操作回执未确认；不要自动重发'}))});
  });
  socket.on('close',()=>{if(this.downstream!==socket)return;this.downstream=undefined;if(!this.closed&&this.online)this.reconnect=setTimeout(()=>this.connectAI(),2000)});
 }
 async dispatch(action,params,timeout=12000,allowUnverified=false){
  const socket=this.upstream;
  if(this.closed||!socket||socket.readyState!==this.Socket.OPEN||!allowUnverified&&!this.online)throw Error('QQ protocol unavailable');
  if(this.pending.size>=64)throw Error('QQ protocol busy');
  const echo='fishbot:'+randomUUID();this.lastActivity=Date.now();
  return new Promise((resolve,reject)=>{
   const timer=setTimeout(()=>{this.pending.delete(echo);reject(Error('QQ operation outcome unknown'))},timeout);
   this.pending.set(echo,{upstream:socket,resolve,reject,timer});
   try{socket.send(JSON.stringify({action,params,echo}))}catch{clearTimeout(timer);this.pending.delete(echo);reject(Error('QQ send outcome unknown'))}
  });
 }
 async call(action,params,timeout=12000,allowUnverified=false){const result=await this.dispatch(action,params,timeout,allowUnverified);if(result.status!=='ok'||result.retcode!==0)throw Error('QQ action failed or unconfirmed');return result.data}
 disconnectUpstream(){
  const socket=this.upstream;this.upstream=undefined;this.uin='';
  for(const entry of this.pending.values()){clearTimeout(entry.timer);entry.reject(Error('QQ connection changed; outcome unknown'))}this.pending.clear();
  this.initialEvents=undefined;clearTimeout(this.reconnect);const ai=this.downstream;this.downstream=undefined;ai?.close();socket?.close();
 }
 close(){this.closed=true;clearTimeout(this.reconnect);this.disconnectUpstream()}
}
