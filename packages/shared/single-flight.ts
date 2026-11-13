/** One owned asynchronous job; overlapping callers join the same settlement.
 * Scheduling, cancellation, retries and error policy stay with the owner. */
export class SingleFlight{
 pending?:Promise<void>;
 run(work:()=>Promise<void>):Promise<void>{
  if(this.pending)return this.pending;
  let resolve!:()=>void,reject!:(error:unknown)=>void;
  const settled=new Promise<void>((yes,no)=>{resolve=yes;reject=no});
  const job=settled.finally(()=>{if(this.pending===job)this.pending=undefined});
  // Publish ownership before invoking work, including a reentrant caller.
  this.pending=job;
  try{Promise.resolve(work()).then(resolve,reject)}catch(error){reject(error)}
  return job;
 }
}
