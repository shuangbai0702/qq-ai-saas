import {useEffect,useRef} from 'react';
import {Moon} from 'lucide-react';
import {mountPaperExperience} from './runtime.js';
import './paper.css';

// The existing application keeps ownership of every form, route and action.
export function PaperExperience(){
 const ground=useRef<HTMLCanvasElement>(null),drawing=useRef<HTMLCanvasElement>(null),motion=useRef<HTMLButtonElement>(null);
 useEffect(()=>{
  if(!ground.current||!drawing.current||!motion.current)return;
  return mountPaperExperience({ground:ground.current,drawing:drawing.current,motion:motion.current});
 },[]);
 return <><div className="paper-world" aria-hidden="true"><canvas ref={ground} className="paper-ground"/><canvas ref={drawing} className="paper-drawing"/></div><button ref={motion} className="paper-motion" type="button" aria-label="暂停纸面动态" aria-pressed="true"><i/><span>动态开启</span><Moon size={12}/></button></>;
}
