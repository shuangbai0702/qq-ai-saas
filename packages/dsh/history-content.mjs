/** Request-only materialization of canonical history references.
 * The native Session stores tool metadata, never a second raw history/image
 * archive. Other tool messages and their physical boundaries stay untouched.
 */
const prefix='Untrusted external result:\n';
const kinds=new Map([['qq_history_search','qq_history_page'],['qq_history_image','qq_history_image']]);
export async function prepareHistoryModel(options,{resolve,registerImages,assertCurrent}){
 const calls=new Map();
 for(const message of options.messages)if(message.role==='assistant')for(const block of message.content||[])
  if(block.type==='tool-call'&&kinds.has(block.name))calls.set(block.id,kinds.get(block.name));
 const messages=[];
 for(const message of options.messages){
  const kind=message.role==='tool'&&!message.isError?calls.get(message.toolCallId):undefined;
  if(!kind){messages.push(message);continue;}
  const content=[];
  for(const block of message.content){
   if(block.type!=='text'||!block.text.startsWith(prefix)){content.push(block);continue;}
   let reference;try{reference=JSON.parse(block.text.slice(prefix.length))}catch{content.push(block);continue;}
   if(reference?.kind!==kind){content.push(block);continue;}
   let value;
   try{value=await resolve(reference);await assertCurrent();}
   catch(error){await assertCurrent();content.push({type:'text',text:'历史来源已不可读取或已退休；没有恢复旧原文或图片，不能据此发表。'});continue;}
   if(typeof value?.text!=='string'||value.text.length>65536||value.images&&(!Array.isArray(value.images)||value.images.length!==1))throw Error('Invalid history material');
   content.push({type:'text',text:prefix+value.text});
   if(value.images)content.push(...registerImages(value.images));
  }
  messages.push({...message,content});
 }
 return {...options,messages};
}
