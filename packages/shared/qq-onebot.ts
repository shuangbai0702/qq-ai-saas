/** Shared native OneBot transport; business authorization belongs to the caller. */
import {readFile} from 'node:fs/promises';
import path from 'node:path';
type Bot={id:string;protocol:string};
const invalid=(message:string,statusCode=400)=>Object.assign(new Error(message),{statusCode});
export async function nativeCall(root:string,bot:Bot,action:string,params:unknown,signal:AbortSignal){
 if(bot.protocol!=='napcat'&&bot.protocol!=='llbot')throw invalid('当前协议端尚不支持QQ原生操作',409);
 const m=JSON.parse(await readFile(path.join(root,'bots',bot.id,bot.protocol+'-platform.json'),'utf8'));if(m.botId!==bot.id||m.management?.host!=='127.0.0.1'||!Number.isInteger(m.management.port)||m.management.port<1024||m.management.port>65534||!/^[a-z_]{1,64}$/.test(action))throw invalid('协议实例尚未就绪',409);
 const llbot=bot.protocol==='llbot',port=m.management.port+(llbot?0:1),route=llbot?'actions/':'';
 const response=await fetch(`http://127.0.0.1:${port}/${route}${action}`,{method:'POST',redirect:'error',headers:{'Content-Type':'application/json',Authorization:'Bearer '+(llbot?m.management.token:m.reverseWs.token)},body:JSON.stringify(params),signal:AbortSignal.any([signal,AbortSignal.timeout(bot.protocol==='llbot'&&action==='fetch_custom_face_detail'?600000:12000)])});
 const result:any=await response.json();if(!response.ok||result.status!=='ok'||result.retcode!==0)throw invalid('QQ协议未确认操作成功，请确认QQ实际结果；不要自动重发',409);return result.data;
}
