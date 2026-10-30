const variants=[['A · 海盐工作台','A-seasalt-workspace.png'],['B · 午后小屋','B-afternoon-cottage.png'],['C · 深海夜航','C-deepsea-night.png'],['D · 角色编辑部','D-persona-editorial.png']];
const dialog=document.querySelector('#viewer'),image=document.querySelector('#full-image'),title=document.querySelector('#viewer-title'),original=document.querySelector('#original');let current=0;
function display(index){current=(index+variants.length)%variants.length;const [name,source]=variants[current];image.src=source;image.alt=name+' 完整设计效果图';title.textContent=name;original.href=source}
document.querySelectorAll('[data-index]').forEach(button=>button.addEventListener('click',()=>{display(Number(button.dataset.index));dialog.showModal()}));
document.querySelector('#close').addEventListener('click',()=>dialog.close());
document.querySelector('#previous').addEventListener('click',()=>display(current-1));document.querySelector('#next').addEventListener('click',()=>display(current+1));
dialog.addEventListener('keydown',event=>{if(event.key==='ArrowLeft'){event.preventDefault();display(current-1)}if(event.key==='ArrowRight'){event.preventDefault();display(current+1)}});
dialog.addEventListener('click',event=>{if(event.target===dialog){const box=dialog.getBoundingClientRect();if(event.clientX<box.left||event.clientX>box.right||event.clientY<box.top||event.clientY>box.bottom)dialog.close()}});
