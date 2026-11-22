import type {FastifyInstance} from 'fastify';
import {randomUUID} from 'node:crypto';
import {z} from 'zod';
import type {Sql} from '../../../packages/database/index.js';
import {LibraryCardInputSchema,LibraryCardKindSchema} from '../../../packages/contracts/character-cards.js';

const fail=(message:string,statusCode=400):never=>{throw Object.assign(new Error(message),{statusCode})};
// Covers are browser-decoded, resized still WebP files. No remote fetch or
// model input is involved. Validate the binary size and encoded dimensions.
function validateCover(value:string|null|undefined){
 if(value==null)return value;
 if(!/^data:image\/webp;base64,[A-Za-z0-9+/]+={0,2}$/.test(value))fail('封面格式无效，请重新选择图片');
 const data=Buffer.from(value.split(',')[1],'base64');
 if(data.length<30||data.length>131072||data.toString('ascii',0,4)!=='RIFF'||data.toString('ascii',8,12)!=='WEBP'||data.readUInt32LE(4)!==data.length-8)fail('封面文件无效或过大');
 let dimensions=false;
 for(let at=12;at+8<=data.length;){const kind=data.toString('ascii',at,at+4),size=data.readUInt32LE(at+4),start=at+8;if(start+size>data.length)fail('封面文件不完整');let width=0,height=0;
  if(kind==='VP8 '&&size>=10&&data.toString('hex',start+3,start+6)==='9d012a'){width=data.readUInt16LE(start+6)&16383;height=data.readUInt16LE(start+8)&16383}
  if(kind==='VP8L'&&size>=5&&data[start]===47){const bits=data.readUInt32LE(start+1);width=(bits&16383)+1;height=((bits>>>14)&16383)+1}
  if(kind==='VP8X'&&size>=10){if(data[start]&2)fail('封面请使用静态图片');width=data.readUIntLE(start+4,3)+1;height=data.readUIntLE(start+7,3)+1}
  if(kind==='ANIM'||kind==='ANMF')fail('封面请使用静态图片');
  if(width&&height){if(width>1024||height>1024)fail('封面尺寸过大');dimensions=true}
  at=start+size+(size%2);
 }
 if(!dimensions)fail('封面图片无效');return value;
}

export function registerCharacterCardLibrary(app:FastifyInstance,db:Sql){
 const columns=`c.id,c.name,c.kind,c.description,c.revision,c.cover IS NOT NULL AS has_cover,c.owner_id,u.name AS author_name`;
 const present=(row:any,userId:string)=>({id:row.id,name:row.name,kind:row.kind,description:row.description,revision:row.revision,authorName:row.author_name,editable:row.owner_id===userId,coverUrl:row.has_cover?`/v1/character-card-library/${row.id}/cover?revision=${row.revision}`:null,...(row.content===undefined?{}:{content:row.content})});
 const idOf=(req:any)=>z.uuid().parse(req.params.id);
 const lockOwner=async(tx:Sql,req:any)=>{await tx.query('SELECT id FROM users WHERE id=$1 FOR UPDATE',[req.user.id])};
 const owned=async(tx:Sql,req:any,id:string)=>{const [row]=await tx.query('SELECT * FROM character_card_library WHERE id=$1 AND owner_id=$2',[id,req.user.id]);if(!row)fail('角色卡不存在或不属于你',404);return row};
 const audit=async(tx:Sql,req:any,event:string,id:string,revision:number)=>{await tx.query('INSERT INTO audit_events(tenant_id,event,detail) VALUES($1,$2,$3)',[req.user.tenant_id,event,JSON.stringify({actorId:req.user.id,cardId:id,revision})])};
 app.get('/v1/character-card-library',async req=>{
  const v=z.object({kind:LibraryCardKindSchema.optional(),mine:z.enum(['true','false']).default('false'),page:z.coerce.number().int().min(0).max(10000).default(0)}).strict().parse(req.query);
  const rows=await db.query(`SELECT ${columns} FROM character_card_library c JOIN users u ON u.id=c.owner_id WHERE ($1::text IS NULL OR c.kind=$1) AND ($2::boolean=false OR c.owner_id=$3) ORDER BY c.created_at DESC,c.id DESC LIMIT 25 OFFSET $4`,[v.kind||null,v.mine==='true',req.user!.id,v.page*24]);
  const [count]=await db.query('SELECT count(*)::int AS count FROM character_card_library WHERE owner_id=$1',[req.user!.id]);
  return{items:rows.slice(0,24).map(row=>present(row,req.user!.id)),hasMore:rows.length>24,mineCount:count.count};
 });
 app.get<{Params:{id:string}}>('/v1/character-card-library/:id',async req=>{const [row]=await db.query(`SELECT ${columns},c.content FROM character_card_library c JOIN users u ON u.id=c.owner_id WHERE c.id=$1`,[idOf(req)]);if(!row)fail('角色卡不存在',404);return present(row,req.user!.id)});
 app.get<{Params:{id:string}}>('/v1/character-card-library/:id/cover',async(req,reply)=>{const [row]=await db.query('SELECT cover FROM character_card_library WHERE id=$1',[idOf(req)]);if(!row?.cover)fail('封面不存在',404);return reply.type('image/webp').send(Buffer.from(row.cover.split(',')[1],'base64'))});
 app.post('/v1/character-card-library',{bodyLimit:262144},async(req,reply)=>{
  const v=LibraryCardInputSchema.extend({id:z.uuid().optional()}).strict().parse(req.body);validateCover(v.cover);const id=v.id||randomUUID();
  const result=await db.transaction(async tx=>{await lockOwner(tx,req);
   const [prior]=await tx.query('SELECT * FROM character_card_library WHERE id=$1',[id]);
   if(prior){if(prior.owner_id===req.user!.id&&prior.name===v.name&&prior.kind===v.kind&&prior.description===v.description&&prior.content===v.content&&prior.cover===(v.cover??null))return{id,revision:prior.revision,replayed:true};fail('这次创建已提交，内容已变化，请刷新卡库',409)}
   const rows=await tx.query('SELECT slot FROM character_card_library WHERE owner_id=$1',[req.user!.id]);const slot=[1,2,3].find(n=>!rows.some(row=>row.slot===n));if(!slot)fail('每个用户最多创建3张公共角色卡，请先删除自己的卡片',409);
   const created=await tx.query('INSERT INTO character_card_library(id,owner_id,slot,name,kind,description,content,cover) VALUES($1,$2,$3,$4,$5,$6,$7,$8) ON CONFLICT(id) DO NOTHING RETURNING id',[id,req.user!.id,slot,v.name,v.kind,v.description,v.content,v.cover??null]);if(!created.length)fail('这张角色卡已存在，请刷新卡库',409);await audit(tx,req,'character_card_library.created',id,1);return{id,revision:1,replayed:false};
  });reply.code(result.replayed?200:201);return result;
 });
 app.put<{Params:{id:string}}>('/v1/character-card-library/:id',{bodyLimit:262144},async req=>{
  const id=idOf(req),v=LibraryCardInputSchema.extend({expectedRevision:z.number().int().positive()}).strict().parse(req.body);validateCover(v.cover);
  return db.transaction(async tx=>{await lockOwner(tx,req);const prior=await owned(tx,req,id);if(prior.revision!==v.expectedRevision)fail('角色卡已被其他操作修改，请先重新载入',409);const cover=v.cover===undefined?prior.cover:v.cover;
   if(prior.name===v.name&&prior.kind===v.kind&&prior.description===v.description&&prior.content===v.content&&prior.cover===cover)return{id,revision:prior.revision,unchanged:true};
   const revision=prior.revision+1;await tx.query('UPDATE character_card_library SET name=$2,kind=$3,description=$4,content=$5,cover=$6,revision=$7,updated_at=now() WHERE id=$1',[id,v.name,v.kind,v.description,v.content,cover,revision]);await audit(tx,req,'character_card_library.updated',id,revision);return{id,revision};
  });
 });
 app.delete<{Params:{id:string}}>('/v1/character-card-library/:id',async req=>{const id=idOf(req),v=z.object({expectedRevision:z.number().int().positive()}).strict().parse(req.body);return db.transaction(async tx=>{await lockOwner(tx,req);const row=await owned(tx,req,id);if(row.revision!==v.expectedRevision)fail('角色卡已变化，请刷新后再删除',409);await tx.query('DELETE FROM character_card_library WHERE id=$1',[id]);await audit(tx,req,'character_card_library.deleted',id,row.revision);return{ok:true}})});
}
