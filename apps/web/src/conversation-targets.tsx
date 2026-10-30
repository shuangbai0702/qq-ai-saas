import {useState} from 'react';

export function ConversationTargets({values,onChange}:{values:string[];onChange:(values:string[])=>void}){
 const [kind,setKind]=useState('private'),[number,setNumber]=useState(''),[error,setError]=useState('');
 function add(){if(!/^\d{5,20}$/.test(number)){setError('请填写5–20位数字QQ号或群号');return}const value=kind+':'+number;if(values.includes(value)){setError('这个目标已经添加');return}if(values.length>=50){setError('最多添加50个目标');return}onChange([...values,value]);setNumber('');setError('')}
 return <div className="target-editor"><h3>允许主动找谁</h3><p>只对已有聊天记录的目标生效。添加后还需保存并应用。</p>{values.length===0&&<p className="dependency-note">尚未添加目标，开启主动联系后也不会发送。</p>}
 {values.map(value=><div className="button-row" key={value}><span>{value.startsWith('group:')?'群聊':'私聊'} {value.split(':')[1]}</span><button type="button" className="secondary" onClick={()=>onChange(values.filter(v=>v!==value))}>移除 {value.split(':')[1]}</button></div>)}
 <div className="form-grid"><label className="field"><span>目标类型</span><select value={kind} onChange={e=>setKind(e.target.value)}><option value="private">私聊</option><option value="group">群聊</option></select></label><label className="field"><span>QQ号或群号</span><input inputMode="numeric" maxLength={20} value={number} onChange={e=>setNumber(e.target.value.replace(/\D/g,''))}/></label></div><button type="button" className="secondary" onClick={add}>添加主动目标</button>{error&&<p role="alert" className="alert">{error}</p>}
 </div>;
}
