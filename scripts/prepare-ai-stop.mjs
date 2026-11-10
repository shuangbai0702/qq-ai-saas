/** Close worker admission atomically before its ProcessHost owner stops it.
 * The legacy opt-in exists only for installing this contract the first time;
 * it does not claim to remove the old worker's sampling race.
 */
export async function prepareAiStop(agent,group,expected,{allowLegacy=false}={}){
 const health=await agent.shared.request(group,'/health');
 if(health.pid!==expected.pid||health.generation!==group.generation)throw Error('AI stop owner changed');
 if(health.stopFence!==true){
  if(!allowLegacy)throw Error('Worker has no atomic stop fence; replacement deferred');
  if(health.activity?.idle!==true)throw Error('Legacy worker busy; replacement deferred');
  return {legacy:true,cancel:async()=>{}};
 }
 // Mark the attempt before awaiting HTTP: a lost response may have fenced it.
 let attempted=true;
 const cancel=async()=>{
  if(!attempted)return;
  const owner=await agent.host.record(group.id,'ai');
  if(owner?.pid!==expected.pid||owner.created!==expected.created)return;
  await agent.shared.request(group,'/cancel-stop','POST',{});
  attempted=false;
 };
 try{
  const result=await agent.shared.request(group,'/prepare-stop','POST',{});
  if(result.pid!==expected.pid||result.quiescing!==true)throw Error('AI stop fence was not acquired');
  const owner=await agent.host.record(group.id,'ai');
  if(owner?.pid!==expected.pid||owner.created!==expected.created)throw Error('AI stop process identity changed');
  return {legacy:false,cancel};
 }catch(error){
  try{await cancel()}catch{throw new AggregateError([error], 'AI stop failed and admission could not be reopened; operator recovery required')}
  throw error;
 }
}
