import {useEffect,useId,useRef,useState} from 'react';
import {createPortal} from 'react-dom';

type InputField={key:string;label:string;value:string;maxLength:number;required?:boolean};
// Portal avoids nesting this form inside the robot configuration form.
export function EditDialog({title,fields,onSave,onClose}:{title:string;fields:InputField[];onSave:(values:Record<string,string>)=>Promise<void>;onClose:()=>void}){
 const ref=useRef<HTMLDialogElement>(null),pending=useRef(false),live=useRef(false),label=useId();
 const [values,setValues]=useState(()=>Object.fromEntries(fields.map(f=>[f.key,f.value]))),[busy,setBusy]=useState(false),[error,setError]=useState('');
 useEffect(()=>{live.current=true;ref.current?.showModal();return()=>{live.current=false;ref.current?.close()}},[]);
 async function save(){if(pending.current)return;pending.current=true;setBusy(true);setError('');try{await onSave(values);if(live.current)onClose()}catch(e){if(live.current)setError((e as Error).message)}finally{if(live.current){pending.current=false;setBusy(false)}}}
 return createPortal(<dialog ref={ref} className="edit-dialog" aria-labelledby={label} onKeyDown={e=>{if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='s'){e.preventDefault();e.stopPropagation();if(!pending.current)ref.current?.querySelector('form')?.requestSubmit()}}} onCancel={e=>{e.preventDefault();if(!pending.current)onClose()}}><h2 id={label}>{title}</h2><form onSubmit={e=>{e.preventDefault();void save()}}><fieldset disabled={busy}>{fields.map(f=><label className="field" key={f.key}><span>{f.label}</span><textarea rows={3} required={f.required} maxLength={f.maxLength} value={values[f.key]} onChange={e=>setValues({...values,[f.key]:e.target.value})}/><small>{values[f.key].length} / {f.maxLength}</small></label>)}{error&&<p className="alert" role="alert">{error}</p>}<div className="button-row"><button className="secondary" type="button" onClick={onClose}>取消</button><button className="primary" type="submit">{busy?'正在保存…':'保存修改'}</button></div></fieldset></form></dialog>,document.body);
}
