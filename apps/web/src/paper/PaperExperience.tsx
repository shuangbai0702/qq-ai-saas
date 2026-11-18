import {useEffect,useRef} from 'react';
import {Moon} from 'lucide-react';
import {mountPaperExperience} from './runtime.js';

// The existing application keeps ownership of every form, route and action.
export function PaperExperience({wallpaperColour}:{wallpaperColour?:number}={}){
 const ground=useRef<HTMLCanvasElement>(null),drawing=useRef<HTMLCanvasElement>(null),motion=useRef<HTMLButtonElement>(null);
 const runtime=useRef<ReturnType<typeof mountPaperExperience>|null>(null);
 useEffect(()=>{
  if(!ground.current||!drawing.current||!motion.current)return;
  const mounted=mountPaperExperience({ground:ground.current,drawing:drawing.current,motion:motion.current});
  runtime.current=mounted;
  return ()=>{runtime.current=null;mounted.destroy();};
 },[]);
 useEffect(()=>{runtime.current?.setWallpaperColour(wallpaperColour);},[wallpaperColour]);
 return <><div className="paper-world" aria-hidden="true"><canvas ref={ground} className="paper-ground"/><canvas ref={drawing} className="paper-drawing" data-wallpaper-static={wallpaperColour===undefined?undefined:''}/></div><button ref={motion} className="paper-motion" type="button" aria-label="暂停纸面动态" aria-pressed="true"><i/><span>动态开启</span><Moon size={12}/></button></>;
}
