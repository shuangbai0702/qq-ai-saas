import {Component,type ReactNode} from 'react';
import {createRoot} from 'react-dom/client';
import {PaperExperience} from './PaperExperience';
class DecorationBoundary extends Component<{children:ReactNode},{failed:boolean}>{
 state={failed:false};static getDerivedStateFromError(){return {failed:true}}
 render(){return this.state.failed?null:this.props.children}
}
export function startPaper(){
 if(typeof ResizeObserver!=='function'||typeof IntersectionObserver!=='function'||typeof MutationObserver!=='function')return;
 const host=document.createElement('div');host.id='paper-decoration';document.body.append(host);
 const root=createRoot(host);root.render(<DecorationBoundary><PaperExperience/></DecorationBoundary>);
 addEventListener('pagehide',event=>{if(!(event as PageTransitionEvent).persisted){root.unmount();host.remove()}},{once:true});
}
