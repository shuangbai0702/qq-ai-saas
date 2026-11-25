import {Component,Suspense,type ReactNode} from 'react';

// Keep the page shell and its draft owner mounted if a route chunk fails.
export class PageContent extends Component<{children:ReactNode;page:string},{page:string;failed:boolean}>{
 state={page:this.props.page,failed:false};
 static getDerivedStateFromProps(props:{page:string},state:{page:string}){return props.page===state.page?null:{page:props.page,failed:false}}
 static getDerivedStateFromError(){return {failed:true}}
 render(){return this.state.failed?<div className="empty" role="alert"><h2>页面资源加载失败</h2><p>请检查网络后重新加载。若有未保存配置，可先切回配置页处理。</p><button className="secondary" onClick={()=>location.reload()}>重新加载</button></div>:<Suspense fallback={<div className="empty" role="status">正在加载页面…</div>}>{this.props.children}</Suspense>}
}
