import {useLayoutEffect,useRef,type ReactNode} from 'react';
import {ChevronRight} from 'lucide-react';

/** Navigation changes presentation only; the owning page keeps its draft. */
export function CapabilityLayout({label,items,active,onSelect,children}:{label:string;items:{id:string;label:string;group:string}[];active:string;onSelect:(id:string)=>void;children:ReactNode}){
 const content=useRef<HTMLDivElement>(null),previous=useRef(active);
 useLayoutEffect(()=>{
  if(previous.current!==active&&content.current){
   const layout=content.current.parentElement;
   if(layout&&getComputedStyle(layout).gridTemplateColumns.split(' ').length===1)content.current.scrollIntoView({block:'start'});
  }
  previous.current=active;
 },[active]);
 return <div className="config-layout capability-page">
  <aside className="config-nav"><nav aria-label={label+'分区'}>
   {[...new Set(items.map(item=>item.group))].map(group=><div className="config-nav-group" key={group}>
    <h3>{group}</h3>{items.filter(item=>item.group===group).map(item=><button key={item.id} type="button" className={active===item.id?'active':''} aria-pressed={active===item.id} onClick={()=>onSelect(item.id)}>{item.label}<ChevronRight size={14}/></button>)}
   </div>)}
  </nav></aside>
  <div className="config-main" ref={content}>{children}</div>
 </div>;
}
