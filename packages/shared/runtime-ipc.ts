/** Loopback JSON transport only. Discovery, generation/permission admission,
 * status mapping and response semantics belong to each runtime client. */
export async function runtimeIpc(port:number,route:string,options:{method:string;headers:Record<string,string>;body?:unknown;timeoutMs:number}){
 const {method,headers,body,timeoutMs}=options;
 return fetch(`http://127.0.0.1:${port}${route}`,{method,headers,body:body===undefined?undefined:JSON.stringify(body),signal:AbortSignal.timeout(timeoutMs)});
}
