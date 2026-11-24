// Runs inside the one QQ process admitted by this Bot's ProcessHost job.
const fs=require('node:fs');
const path=require('node:path');
module.exports=function prepareQQ(manifestPath){
 const manifest=JSON.parse(fs.readFileSync(manifestPath,'utf8'));
 const home=path.join(manifest.root,'llbot','qq'),profile=path.join(home,'profile');
 for(const dir of [home,profile,path.join(home,'AppData','Roaming'),path.join(home,'AppData','Local')])fs.mkdirSync(dir,{recursive:true});
 Object.assign(process.env,{HOME:home,USERPROFILE:home,APPDATA:path.join(home,'AppData','Roaming'),LOCALAPPDATA:path.join(home,'AppData','Local')});
 if(manifest.authMode==='local')for(const key of ['HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','http_proxy','https_proxy','all_proxy','NO_PROXY','no_proxy'])delete process.env[key];
 // The PMHQ loader prefers these native-discovered Windows paths over HOME.
 // Keep its account/global metadata and downloads inside the same Bot too.
 if(globalThis.PMHQ){globalThis.PMHQ.windowsDocumentPath=path.join(home,'Documents');globalThis.PMHQ.windowsDownloadPath=path.join(home,'Downloads')}
 const {app}=require('electron');app.setPath('userData',profile);app.setPath('appData',path.join(home,'AppData','Roaming'));
 let isolated=false,network=false;
 function proof(){if(isolated&&network){const file=path.join(home,'profile-proof.json');fs.writeFileSync(file+'.tmp',JSON.stringify({botId:manifest.botId,generation:manifest.generation,pid:process.pid,profile,route:manifest.egress.route,network:true}),{mode:0o600});fs.renameSync(file+'.tmp',file)}}
 function facade(original,overrides){
  const value=function(...args){return Reflect.construct(original,args,new.target||original)};Object.setPrototypeOf(value,original);
  for(const key of Object.getOwnPropertyNames(original))if(!['name','length','prototype','caller','arguments',...Object.keys(overrides)].includes(key)&&typeof original[key]==='function')Object.defineProperty(value,key,{value:original[key].bind(original)});
  for(const [key,fn] of Object.entries(overrides))Object.defineProperty(value,key,{value:fn});return value;
 }
 const configured=new WeakSet();
 function configure(session){
  if(!session||configured.has(session))return session;
  const service=session.getMSFService();
  const proxy=manifest.egress.route==='huawei';
  const result=service.setNetworkProxy({userName:'',userPwd:'',address:proxy?'127.0.0.1':'',port:proxy?manifest.egress.proxyPort:0,proxyType:proxy?2:0,domain:'',isSocket:proxy});
  configured.add(session);
  Promise.resolve(result).then(()=>{network=true;proof()},()=>{configured.delete(session)});
  return session;
 }
 const original=process.dlopen;
 process.dlopen=function(module,filename,...args){
  const result=Reflect.apply(original,this,[module,filename,...args]);
  const util=module.exports?.NodeQQNTWrapperUtil;
  if(util&&typeof util.getNTUserDataInfoConfig==='function'){
   module.exports.NodeQQNTWrapperUtil=facade(util,{getNTUserDataInfoConfig:()=>profile});isolated=true;proof();
  }
  const sessions=module.exports?.NodeIQQNTWrapperSession;
  if(sessions){const overrides={};for(const name of ['getNTWrapperSession','create'])if(typeof sessions[name]==='function')overrides[name]=(...values)=>configure(sessions[name](...values));
   module.exports.NodeIQQNTWrapperSession=facade(sessions,overrides);
  }
  return result;
 };
};
