export function createRequestBudget(now=Date.now){
 const buckets=new Map<string,{until:number;count:number;active:number}>();
 const denied=()=>Object.assign(new Error('操作过于频繁，请稍后重试'),{statusCode:429});
 return{acquire(keys:string[],max:number,concurrency=2){const time=now();for(const[key,row]of buckets)if(row.until<=time&&row.active===0)buckets.delete(key);
 const rows=keys.map(key=>{let row=buckets.get(key);if(!row){if(buckets.size>=10000)throw denied();row={until:time+60000,count:0,active:0};buckets.set(key,row)}else if(row.until<=time){row.until=time+60000;row.count=0}return row});
 if(rows.some(row=>row.count>=max||row.active>=concurrency))throw denied();for(const row of rows){row.count++;row.active++}let released=false;return()=>{if(released)return;released=true;for(const row of rows)row.active--};},clear(){buckets.clear()}};
}
