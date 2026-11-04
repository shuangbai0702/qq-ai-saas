import {constants,publicEncrypt} from 'node:crypto';
import type {Sql} from '../../../../packages/database/index.js';
import {encrypt,decrypt} from '../../../../packages/shared/security.js';
import {BiliWeb,BiliError,cookieCandidate,type Credential} from './web-adapter.js';
// Platform public key, also documented by Iseenope/bilibili-mcp-server
// 20eedd09862aea2c160163007f45221c544651b2 (MIT). No SDK/global cookie store.
const refreshKey=`-----BEGIN PUBLIC KEY-----
MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQDLgd2OAkcGVtoE3ThUREbio0Eg
Uc/prcajMKXvkCKFCWhJYJcLkcM2DKKcSeFpD/j6Boy538YXnR6VhcuUJOhH2x71
nzPjfdTcqMz7djHum0qSZA0AyCBDABUqCrfNgCiJ00Ra7GmRj+YCK1NJEuewlb40
JNrRuoEUXpabUzGB8QIDAQAB
-----END PUBLIC KEY-----`;
export class BiliAccounts {
 constructor(public db:Sql,private key:Buffer,public web:BiliWeb){}
 scope(tenant:string,bot:string,kind='account'){return`bilibili:${kind}:${tenant}:${bot}`}
 seal(tenant:string,bot:string,value:unknown,kind='account'){return encrypt(this.key,this.scope(tenant,bot,kind),JSON.stringify(value))}
 open(tenant:string,bot:string,value:string,kind='account'){return JSON.parse(decrypt(this.key,this.scope(tenant,bot,kind),value))}
 async credential(tenant:string,bot:string){const[a]=await this.db.query('SELECT * FROM bilibili_accounts WHERE tenant_id=$1 AND bot_id=$2',[tenant,bot]);if(!a||a.state!=='ready'||!['idle','confirm_pending'].includes(a.refresh_state))throw new BiliError('auth_required','账号未就绪，请重新扫码或完成刷新');return{record:a,credential:this.open(tenant,bot,a.secret) as Credential}}
 async tick(signal:AbortSignal){
  const [login]=await this.db.query("SELECT * FROM bilibili_logins WHERE state IN ('created','waiting_scan','waiting_confirm') AND updated_at<now()-interval '3 seconds' ORDER BY updated_at LIMIT 1");
  if(login){await this.poll(login,signal);return}
  // Existing Agent tick checks one due account; no per-account timer.
  await this.db.query("UPDATE bilibili_accounts SET refresh_state='requested',updated_at=now() WHERE bot_id=(SELECT bot_id FROM bilibili_accounts WHERE state='ready' AND refresh_state='idle' AND updated_at<now()-interval '24 hours' ORDER BY updated_at LIMIT 1) AND refresh_state='idle'");
  const [account]=await this.db.query("SELECT * FROM bilibili_accounts WHERE refresh_state='requested' OR refresh_state='confirm_pending' AND updated_at<now()-interval '30 seconds' ORDER BY updated_at LIMIT 1");
  if(account)await this.refresh(account,signal);
 }
 async poll(row:any,signal:AbortSignal){
  const {tenant_id:t,bot_id:b,id}=row;let state='failed';
  try{
   if(+new Date(row.expires_at)<Date.now())state='expired';
   else if(row.state==='created'){
    const r=await this.web.request('https://passport.bilibili.com/x/passport-login/web/qrcode/generate',{},undefined,signal),url=new URL(r.data.url);
    if(url.protocol!=='https:'||url.hostname!=='account.bilibili.com'||!/^[-\w]{1,256}$/.test(r.data.qrcode_key))throw new BiliError('invalid_qr','二维码响应无效');
    await this.db.query("UPDATE bilibili_logins SET state='waiting_scan',secret=$4,updated_at=now() WHERE bot_id=$1 AND tenant_id=$2 AND id=$3 AND state='created'",[b,t,id,this.seal(t,b,{key:r.data.qrcode_key,url:url.href},'login')]);return;
   }else{
    const qr=this.open(t,b,row.secret,'login'),r=await this.web.request('https://passport.bilibili.com/x/passport-login/web/qrcode/poll',{qrcode_key:qr.key},undefined,signal);
    if(r.data.code===86101)state='waiting_scan';else if(r.data.code===86090)state='waiting_confirm';else if(r.data.code===86038)state='expired';
    else if(r.data.code===0){
     const candidate=cookieCandidate(r.headers,String(r.data.refresh_token||'')),self=await this.web.self(candidate,signal);
     await this.db.transaction(async tx=>{
      const [current]=await tx.query('SELECT * FROM bilibili_logins WHERE bot_id=$1 AND tenant_id=$2 FOR UPDATE',[b,t]);
      if(!current||current.id!==id||!['waiting_scan','waiting_confirm'].includes(current.state)||+new Date(current.expires_at)<Date.now()||signal.aborted)return;
      const [old]=await tx.query('SELECT uid FROM bilibili_accounts WHERE bot_id=$1 AND tenant_id=$2',[b,t]);
      if(old&&old.uid!==self.uid)throw new BiliError('identity_mismatch','请先解除旧账号绑定，再登录其他账号');
      await tx.query("INSERT INTO bilibili_accounts(bot_id,tenant_id,uid,label,secret) VALUES($1,$2,$3,$4,$5) ON CONFLICT(bot_id) DO UPDATE SET uid=$3,label=$4,secret=$5,revision=bilibili_accounts.revision+1,state='ready',refresh_state='idle',pending_confirm=NULL,updated_at=now()",[b,t,self.uid,self.label,this.seal(t,b,candidate)]);
      await tx.query("UPDATE bilibili_logins SET state='ready',secret='',updated_at=now() WHERE bot_id=$1 AND tenant_id=$2 AND id=$3",[b,t,id]);
     });return;
    }
   }
  }catch(error){if(signal.aborted)return;state=error instanceof BiliError&&error.code==='risk_control'?'risk_control':error instanceof BiliError&&error.code==='identity_mismatch'?'identity_mismatch':'failed'}
  await this.db.query('UPDATE bilibili_logins SET state=$4,secret=CASE WHEN $4 IN (\'waiting_scan\',\'waiting_confirm\') THEN secret ELSE \'\' END,updated_at=now() WHERE bot_id=$1 AND tenant_id=$2 AND id=$3 AND state IN (\'created\',\'waiting_scan\',\'waiting_confirm\')',[b,t,id,state]);
 }
 async refresh(a:any,signal:AbortSignal){
  const t=a.tenant_id,b=a.bot_id,c=this.open(t,b,a.secret) as Credential;
  if(a.refresh_state==='confirm_pending'){
   try{const old=this.open(t,b,a.pending_confirm,'pending');await this.web.request('https://passport.bilibili.com/x/passport-login/web/confirm/refresh',{refresh_token:old.refreshToken},c,signal,'POST');await this.db.query("UPDATE bilibili_accounts SET refresh_state='idle',pending_confirm=NULL,updated_at=now() WHERE bot_id=$1 AND tenant_id=$2 AND revision=$3 AND refresh_state='confirm_pending'",[b,t,a.revision])}catch{await this.db.query('UPDATE bilibili_accounts SET updated_at=now() WHERE bot_id=$1 AND tenant_id=$2',[b,t])}return;
  }
  let dispatched=false;
  try{
   const claimed=await this.db.query("UPDATE bilibili_accounts SET refresh_state='checking',updated_at=now() WHERE bot_id=$1 AND tenant_id=$2 AND revision=$3 AND refresh_state='requested' RETURNING bot_id",[b,t,a.revision]);if(!claimed.length)return;
   const info=await this.web.request('https://passport.bilibili.com/x/passport-login/web/cookie/info',{csrf:c.cookies.bili_jct},c,signal);
   if(!info.data.refresh){await this.web.self(c,signal);await this.db.query("UPDATE bilibili_accounts SET refresh_state='idle',updated_at=now() WHERE bot_id=$1 AND tenant_id=$2 AND revision=$3",[b,t,a.revision]);return}
   if(!c.refreshToken||!Number.isFinite(info.data.timestamp))throw new BiliError('auth_required','缺少刷新凭证，请扫码');
   const encoded=publicEncrypt({key:refreshKey,padding:constants.RSA_PKCS1_OAEP_PADDING,oaepHash:'sha256'},Buffer.from('refresh_'+info.data.timestamp)).toString('hex');
   const r=await this.web.transport('https://www.bilibili.com/correspond/1/'+encoded,{redirect:'error',signal:AbortSignal.any([signal,AbortSignal.timeout(15000)]),headers:{Cookie:Object.entries(c.cookies).map(([k,v])=>`${k}=${v}`).join('; ')}});
   if(!r.ok){await r.body?.cancel();throw new BiliError('auth_required','刷新验证失败')}
   // Bounded text body (not a video or credential returned to the caller).
   const reader=r.body!.getReader();let html='',bytes=0;try{for(;;){const v=await reader.read();if(v.done)break;bytes+=v.value.length;if(bytes>262144)throw new Error('oversize');html+=Buffer.from(v.value).toString('utf8')}}finally{await reader.cancel().catch(()=>{});reader.releaseLock()}
   const csrf=/<div\s+id="1-name">([^<]+)<\/div>/.exec(html)?.[1];if(!csrf)throw new BiliError('auth_required','刷新验证未返回');
   const armed=await this.db.query("UPDATE bilibili_accounts SET refresh_state='refreshing',updated_at=now() WHERE bot_id=$1 AND tenant_id=$2 AND revision=$3 AND refresh_state='checking' RETURNING bot_id",[b,t,a.revision]);if(!armed.length||signal.aborted)return;
   dispatched=true;const fresh=await this.web.request('https://passport.bilibili.com/x/passport-login/web/cookie/refresh',{refresh_csrf:csrf,refresh_token:c.refreshToken,source:'main_web'},c,signal,'POST');
   const next=cookieCandidate(fresh.headers,String(fresh.data.refresh_token||''));if(!next.refreshToken||next.cookies.DedeUserID!==a.uid)throw new BiliError('identity_mismatch','刷新身份不匹配');
   await this.web.self(next,signal);
   const saved=await this.db.query("UPDATE bilibili_accounts SET secret=$4,revision=revision+1,refresh_state='confirm_pending',pending_confirm=$5,updated_at=now() WHERE bot_id=$1 AND tenant_id=$2 AND revision=$3 AND refresh_state='refreshing' RETURNING *",[b,t,a.revision,this.seal(t,b,next),this.seal(t,b,{refreshToken:c.refreshToken},'pending')]);
   if(saved[0])await this.refresh(saved[0],signal);
  }catch{await this.db.query("UPDATE bilibili_accounts SET refresh_state=$4,state=CASE WHEN $4='refresh_unknown' THEN 'auth_required' ELSE state END,updated_at=now() WHERE bot_id=$1 AND tenant_id=$2 AND revision=$3 AND refresh_state IN ('checking','refreshing')",[b,t,a.revision,dispatched?'refresh_unknown':'auth_required'])}
 }
 async recover(){await this.db.query("UPDATE bilibili_accounts SET refresh_state=CASE WHEN refresh_state='refreshing' THEN 'refresh_unknown' ELSE 'requested' END,state=CASE WHEN refresh_state='refreshing' THEN 'auth_required' ELSE state END WHERE refresh_state IN ('refreshing','checking')")}
}
