import {randomUUID} from 'node:crypto';

/** One socket owns its callbacks and replies; no alternate connection cache. */
export function hardenPmhq(Class,Socket,selfInfo,codec={decode:x=>x,encode:x=>x}){
 const p=Class.prototype;
 p.connectWebSocket=async function(){
  if(this.fishDisposed)return;
  clearTimeout(this.reconnectTimer);
  const previous=this.ws,socket=new Socket(this.wsUrl);this.ws=socket;previous?.close();if(previous)for(const cancel of this.fishPending||[])cancel(Error('PMHQ connection changed; outcome unknown'));
  const current=()=>!this.fishDisposed&&this.ws===socket;
  const disconnect=()=>{
   if(!current())return;
   this.ws=undefined;socket.close();selfInfo.online=false;this.resetPmhqState();
   for(const cancel of this.fishPending||[])cancel(Error('PMHQ connection changed; outcome unknown'));
   this.reconnectTimer=setTimeout(()=>this.connectWebSocket(),1000);
  };
  socket.onerror=disconnect;socket.onclose=disconnect;
  socket.onopen=()=>{if(!current()){socket.close();return}this.hasConnectedOnce=true;this.lastConnectedTime=Date.now();this.startPmhqLoginProbe()};
  socket.onmessage=event=>{
   if(!current())return;let data;
   try{data=codec.decode(JSON.parse(event.data.toString()))}catch{return}
   for(const listener of this.resListeners.values())setImmediate(()=>{if(current())try{listener(data)}catch{this.logger.error('PMHQ listener failed')}});
  };
  if(!this.fishDisposeRegistered){this.fishDisposeRegistered=true;this.ctx.on('dispose',()=>{this.fishDisposed=true;this.pmhqProbeToken++;clearTimeout(this.reconnectTimer);this.ws?.close();this.ws=undefined;for(const cancel of this.fishPending||[])cancel(Error('PMHQ disposed; outcome unknown'));this.resListeners.clear()})}
 };
 p.waitConnected=async function(timeout=15000){
  const end=Date.now()+timeout;
  while(!this.fishDisposed&&Date.now()<end){if(this.ws?.readyState===Socket.OPEN)return true;await new Promise(resolve=>setTimeout(resolve,Math.min(100,end-Date.now())))}
  throw Error('PMHQ connection deadline exceeded');
 };
 p.wsSend=async function(data,timeout=15000){
  const deadline=Date.now()+timeout;
  await this.waitConnected(timeout);const socket=this.ws;
  if(this.fishDisposed||!socket||socket.readyState!==Socket.OPEN)throw Error('PMHQ connection changed');
  const echo=data.data?.echo||randomUUID();data={...data,data:{...data.data,echo}};
  return new Promise((resolve,reject)=>{
   this.fishPending??=new Set();let finished=false;
   const finish=(error,value)=>{if(finished)return;finished=true;clearTimeout(timer);this.removeResListener(listener);this.fishPending.delete(finish);error?reject(error):resolve(value)};
   const listener=this.addResListener(value=>{if(value.data?.echo===echo)finish(undefined,value)});
   const timer=setTimeout(()=>finish(Error('PMHQ response deadline exceeded')),Math.max(1,deadline-Date.now()));
   this.fishPending.add(finish);
   try{socket.send(JSON.stringify(codec.encode(data)))}catch{finish(Error('PMHQ send outcome unknown'))}
  });
 };
 p.httpSend=async function(data,timeout=15000){
  const response=await fetch(this.httpUrl,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(codec.encode(data)),signal:AbortSignal.timeout(timeout)});
  if(!response.ok)throw Error('PMHQ HTTP request failed');return codec.decode(await response.json());
 };
 p.sendPB=async function(cmd,pb,timeout=15000){
  if(this.fishDisposed)throw Error('PMHQ disposed');
  const data={type:'send',data:{cmd,pb:Buffer.isBuffer(pb)?pb.toString('hex'):pb}};
  // A dispatched write is never retried through the other transport.
  const response=await (this.ws?.readyState===Socket.OPEN?this.wsSend(data,timeout):this.httpSend(data,timeout));
  if(this.fishDisposed)throw Error('PMHQ disposed; outcome unknown');
  return this.unwrapPmhqRes(response,cmd);
 };
 p.fetchPmhqQrcode=async function(){const response=await fetch(this.httpUrl+'get_login_qrcode',{signal:AbortSignal.timeout(4000)});if(!response.ok)throw Error('PMHQ QR unavailable');return response.json()};
}

export function hardenReverse(Class,Socket,lifeCycle){
 const p=Class.prototype;
 p.stop=function(){this.activated=false;this.fishEpoch=(this.fishEpoch||0)+1;this.fishReconnect?.();this.fishReconnect=undefined;const socket=this.wsClient;this.wsClient=undefined;socket?.close()};
 p.handleAction=async function(data,socket=this.wsClient){
  let request;try{request=JSON.parse(data.toString())}catch{return}
  if(!this.activated||socket!==this.wsClient)return;
  const action=this.config.actionMap.get(request.action);
  const reply=action?await action.websocketHandle(request.params,request.echo,{messageFormat:this.config.messageFormat,debug:this.config.debug}):{status:'failed',retcode:1404,data:null,echo:request.echo,message:'Unsupported OneBot action'};
  if(this.activated&&socket===this.wsClient)this.reply(socket,reply);
 };
 p.tryConnect=function(){
  if(!this.activated)return;
  const epoch=this.fishEpoch=(this.fishEpoch||0)+1;
  const socket=new Socket(this.config.url,{maxPayload:16*1024*1024,handshakeTimeout:4000,perMessageDeflate:false,headers:lifeCycle.headers(this.config)});this.wsClient=socket;
  const current=()=>this.activated&&this.wsClient===socket&&this.fishEpoch===epoch;
  socket.on('open',()=>{if(current())this.reply(socket,lifeCycle.event())});
  socket.on('message',data=>{if(current())void this.handleAction(data,socket).catch(()=>this.ctx.logger.error('OneBot action outcome unknown'))});
  socket.on('error',()=>{});socket.on('ping',()=>{if(current())socket.pong()});
  const heartbeat=this.ctx.interval(()=>{if(current())this.reply(socket,lifeCycle.heartbeat(this.config))},this.config.heartInterval);
  socket.on('close',()=>{heartbeat();if(!current())return;this.wsClient=undefined;this.fishReconnect=this.ctx.timeout(()=>{if(this.activated&&this.fishEpoch===epoch)this.tryConnect()},3000)});
 };
}
