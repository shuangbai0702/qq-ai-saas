import {Component,Suspense,lazy,useEffect,useRef,useState,type ReactNode} from 'react';
import {createRoot} from 'react-dom/client';
import {api,ApiError} from './api';
import {Auth} from './auth';
import './style.css';
import './paper/paper.css';
const Workspace=lazy(()=>import('./workspace'));
class ScreenBoundary extends Component<{children:ReactNode},{failed:boolean}>{
 state={failed:false};
 static getDerivedStateFromError(){return {failed:true}}
 render(){return this.state.failed?<Failure message="页面资源加载失败，请检查网络后重新加载。"/>:this.props.children}
}
function Failure({message,retry}:{message:string;retry?:()=>void}){return <div className="loading" role="alert"><h2>暂时未能加载</h2><p>{message}</p><button className="secondary" onClick={retry||(()=>location.reload())}>{retry?'重试连接':'重新加载'}</button></div>}
function App(){
 const[user,setUser]=useState<any>(),[loading,setLoading]=useState(true),[error,setError]=useState('');
 const request=useRef<AbortController|null>(null),sequence=useRef(0);
 async function reloadUser(){
  request.current?.abort();const owner=new AbortController(),order=++sequence.current;request.current=owner;setLoading(true);setError('');
  try{const data=await api('/auth/me',undefined,undefined,owner.signal);if(!owner.signal.aborted&&order===sequence.current)setUser(data.user)}
  catch(e){if(owner.signal.aborted||order!==sequence.current)return;if(e instanceof ApiError&&e.status===401)setUser(null);else setError((e as Error).message)}
  finally{if(!owner.signal.aborted&&order===sequence.current)setLoading(false)}
 }
 useEffect(()=>{document.getElementById('root')!.dataset.mounted='true';void reloadUser();return()=>{sequence.current++;request.current?.abort()}},[]);
 if(loading)return <div className="loading" role="status"><h2>正在连接工作区…</h2><p>首次连接可能需要几秒。</p></div>;
 if(error)return <Failure message={error} retry={()=>void reloadUser()}/>;
 if(!user)return <Auth done={()=>void reloadUser()}/>;
 return <Suspense fallback={<div className="loading" role="status"><h2>正在加载工作区…</h2><p>网络较慢时请稍候；若持续无响应，可<a href="" onClick={e=>{e.preventDefault();location.reload()}}>重新加载</a>。</p></div>}><Workspace key={user.id} user={user} onLogout={()=>setUser(null)}/></Suspense>;
}
async function start(){
 // Preserve the configured public-origin redirect without newer AbortSignal APIs.
 if(location.protocol==='http:'&&!['localhost','127.0.0.1','[::1]'].includes(location.hostname)){
  const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),8000);
  try{const response=await fetch('/v1/health',{signal:controller.signal});const info=await response.json();const target=new URL(info.publicOrigin);if(target.protocol==='https:'&&target.origin!==location.origin){target.pathname=location.pathname;target.search=location.search;target.hash=location.hash;location.replace(target.href);return}}catch{/* The UI offers its own connection retry. */}finally{clearTimeout(timer)}
 }
 createRoot(document.getElementById('root')!).render(<ScreenBoundary><App/></ScreenBoundary>);
 // Decoration is optional: small/touch screens keep the static design and avoid GPU work.
 if(!matchMedia('(max-width: 760px), (pointer: coarse), (prefers-reduced-motion: reduce)').matches){
  setTimeout(()=>{void import('./paper/start').then(({startPaper})=>startPaper()).catch(()=>{/* Business UI stays available. */})},2000);
 }
}
void start();
