const variants=[['A · 潮汐工作台 / 我的机器人','A-dashboard-v2.png'],['A · 潮汐工作台 / 基本配置','A-config-v2.png'],['B · 鱼栖编辑部 / 我的机器人','B-dashboard.png'],['B · 鱼栖编辑部 / 基本配置','B-config-v2.png']];
const dialog=document.querySelector('#viewer'),image=document.querySelector('#full-image'),title=document.querySelector('#viewer-title'),original=document.querySelector('#original');let current=0;
function display(index){current=(index+variants.length)%variants.length;const[name,src]=variants[current];image.src=src;image.alt=name+' 完整效果图';title.textContent=name;original.href=src}
document.querySelectorAll('[data-index]').forEach(button=>button.addEventListener('click',()=>{display(Number(button.dataset.index));dialog.showModal()}));
document.querySelector('#close').addEventListener('click',()=>dialog.close());document.querySelector('#previous').addEventListener('click',()=>display(current-1));document.querySelector('#next').addEventListener('click',()=>display(current+1));
dialog.addEventListener('keydown',event=>{if(event.key==='ArrowLeft'){event.preventDefault();display(current-1)}if(event.key==='ArrowRight'){event.preventDefault();display(current+1)}});
dialog.addEventListener('click',event=>{if(event.target===dialog){const r=dialog.getBoundingClientRect();if(event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom)dialog.close()}});
