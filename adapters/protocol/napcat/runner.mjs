/** Private portable NapCat runner. It shares read-only binaries, not QQ profiles. */
import path from 'node:path';
import os from 'node:os';
import fs from 'node:fs';
import {pathToFileURL,fileURLToPath} from 'node:url';

export function prepareRuntime(manifestPath) {
  const manifest=JSON.parse(fs.readFileSync(manifestPath,'utf8'));
  const dataDir=path.resolve(manifest.dataDir),root=path.dirname(path.resolve(manifestPath));
  if(dataDir!==path.join(root,'qq'))throw new Error('NapCat dataDir must belong to the bot manifest directory');
  const runtimeDir=path.resolve(manifest.runtimeDir||'');
  for(const file of ['wrapper.node','package.json','napcat/napcat.mjs',...(process.platform==='win32'?['config.json']:['node_addon.node'])])if(!fs.statSync(path.join(runtimeDir,file)).isFile())throw new Error('Incomplete NapCat runtime: '+file);
  if(manifest.management?.host!=='127.0.0.1'||!Number.isInteger(manifest.management?.port)||!manifest.management?.token)throw new Error('A private loopback management endpoint is required');
  const home=path.join(dataDir,'home'),qqProfile=path.join(dataDir,'profile'),workdir=path.join(dataDir,'napcat');
  for(const dir of [home,qqProfile,workdir,path.join(workdir,'config'),path.join(dataDir,'tmp'),path.join(home,'AppData/Roaming'),path.join(home,'AppData/Local')])fs.mkdirSync(dir,{recursive:true});
  const client={name:'fishbot',enable:true,url:manifest.reverseWs.url,token:manifest.reverseWs.token,messagePostFormat:'array',reportSelfMessage:false,reconnectInterval:2000,heartInterval:30000,debug:false};
  const maintenance={name:'fishbot-local',enable:true,host:'127.0.0.1',port:manifest.management.port+1,token:manifest.reverseWs.token,enableCors:false,enableWebsocket:false,messagePostFormat:'array',debug:false};
  const onebot={network:{httpServers:[maintenance],httpSseServers:[],httpClients:[],websocketServers:[],websocketClients:[client],plugins:[]},musicSignUrl:'',enableLocalFile2Url:false,parseMultMsg:false,imageDownloadProxy:''};
  // New accounts inherit onebot11.json. Existing restored account files are refreshed too.
  const configDir=path.join(workdir,'config');
  const files=new Set(['onebot11.json',...fs.readdirSync(configDir).filter(x=>/^onebot11_\d+\.json$/.test(x))]);
  for(const file of files)fs.writeFileSync(path.join(configDir,file),JSON.stringify(onebot,null,2),{mode:0o600});
  fs.writeFileSync(path.join(configDir,'webui.json'),JSON.stringify({host:'127.0.0.1',port:manifest.management.port,token:manifest.management.token,loginRate:3,accessControlMode:'whitelist',ipWhitelist:['127.0.0.1'],enableXForwardedFor:false}),{mode:0o600});
  return {manifest,runtimeDir,qqProfile,workdir,home,env:{HOME:home,USERPROFILE:home,APPDATA:path.join(home,'AppData/Roaming'),LOCALAPPDATA:path.join(home,'AppData/Local'),XDG_CONFIG_HOME:path.join(home,'.config'),TMP:path.join(dataDir,'tmp'),TEMP:path.join(dataDir,'tmp'),TMPDIR:path.join(dataDir,'tmp'),NAPCAT_WORKDIR:workdir,NAPCAT_DISABLE_MULTI_PROCESS:'1',NAPCAT_DISABLE_PIPE:'1',NAPCAT_FORCE_NODE_PROCESS:'1',NAPCAT_WRAPPER_PATH:path.join(runtimeDir,'wrapper.node'),NAPCAT_QQ_PACKAGE_INFO_PATH:path.join(runtimeDir,'package.json'),...(fs.existsSync(path.join(runtimeDir,'config.json'))?{NAPCAT_QQ_VERSION_CONFIG_PATH:path.join(runtimeDir,'config.json')}:{}),...(process.platform==='linux'?{NAPCAT_PRELOAD_NODE_ADDON_PATH:path.join(runtimeDir,'node_addon.node')}:{} )}};
}

export function isolateNativeProfile(runtime) {
  const original=process.dlopen,wrapper=path.resolve(runtime.env.NAPCAT_WRAPPER_PATH);
  process.dlopen=function(module,filename,...args) {
    const result=Reflect.apply(original,this,[module,filename,...args]);
    if(path.resolve(filename)===wrapper){
      const util=module.exports.NodeQQNTWrapperUtil;
      if(!util||typeof util.getNTUserDataInfoConfig!=='function')throw new Error('Unsupported QQ native wrapper: profile accessor unavailable');
      // HOME is insufficient on Windows: native util can return the desktop user's registry path.
      // Override only this accessor in this process; never edit the desktop registry/profile.
      // The native method itself is non-configurable. Replace the writable export
      // with a facade and bind other static native calls to their original owner.
      const facade=function(...params){return Reflect.construct(util,params,new.target||util)};
      Object.setPrototypeOf(facade,util);
      for(const key of Object.getOwnPropertyNames(util)){
        if(['name','length','prototype','caller','arguments','getNTUserDataInfoConfig'].includes(key))continue;
        const value=util[key];if(typeof value==='function')Object.defineProperty(facade,key,{value:value.bind(util)});
      }
      Object.defineProperty(facade,'getNTUserDataInfoConfig',{value:()=>runtime.qqProfile});
      module.exports.NodeQQNTWrapperUtil=facade;
      if(module.exports.NodeQQNTWrapperUtil.getNTUserDataInfoConfig()!==runtime.qqProfile)throw new Error('QQ profile isolation failed');
    }
    return result;
  };
}

async function main(){
  const manifest=process.argv[2];if(!manifest)throw new Error('Usage: bundled-node runner.mjs /absolute/bot/napcat-platform.json');
  const runtime=prepareRuntime(manifest);Object.assign(process.env,runtime.env);process.chdir(runtime.manifest.dataDir);
  if(os.homedir()!==runtime.home)throw new Error('HOME isolation not effective');
  if(process.platform==='linux')process.dlopen({exports:{}},runtime.env.NAPCAT_PRELOAD_NODE_ADDON_PATH,os.constants.dlopen.RTLD_NOW|os.constants.dlopen.RTLD_GLOBAL);
  isolateNativeProfile(runtime);
  const identityFile=path.join(runtime.manifest.dataDir,'identity.json');
  if(runtime.manifest.resume!==false&&fs.existsSync(identityFile)){const {uin}=JSON.parse(fs.readFileSync(identityFile,'utf8'));if(/^\d{5,20}$/.test(uin)&&(!runtime.manifest.expectedUin||runtime.manifest.expectedUin===uin))process.argv.push('-q',uin)}
  console.log('[fishbot-napcat] private profile prepared; real QQ runtime starting');
  await import(pathToFileURL(path.join(runtime.runtimeDir,'napcat/napcat.mjs')).href);
}
if(process.argv[1]&&path.resolve(process.argv[1])===fileURLToPath(import.meta.url))main().catch(error=>{console.error('[fishbot-napcat]',error.message);process.exitCode=1});
