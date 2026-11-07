// Small helpers: post a form action, then re-render the stage from the server. State lives in SQLite.
function toast(msg){const t=document.getElementById('toast');if(!t)return;t.textContent=msg;t.style.display='block';clearTimeout(window.__tt);window.__tt=setTimeout(()=>t.style.display='none',5000)}
const busy=new Set();
async function act(url,data,after){
  if(busy.has(url))return false;busy.add(url);try{return await act1(url,data,after)}finally{busy.delete(url)}}
async function act1(url,data,after){
  const fd=new FormData();Object.entries(data||{}).forEach(([k,v])=>fd.append(k,v));
  const r=await fetch(url,{method:'POST',body:fd});let j={};try{j=await r.json()}catch(e){}
  if((j.error||'').startsWith('Someone just changed')||(j.error||'').startsWith('This handover is already')){if(after)await after(j);return true}
  if(!r.ok||j.ok===false){toast(j.error||'That did not work.');return false}
  if(after)await after(j);return true}
async function refreshStage(){
  const r=await fetch('/contract?fragment=1');if(!r.ok)return;
  document.getElementById('stage').innerHTML=await r.text();
  const c=await (await fetch('/counter.json')).json();const el=document.getElementById('counter');if(el)el.innerHTML=`Assets: <b>${c.assets}</b> · Views: <b>${c.views}</b>`}

// Interview: the card is already on the page (cached or empty). If a model may answer, ask once and swap the card when it arrives.
(function(){const box=document.getElementById('reading');if(!box||box.dataset.live!=='1')return;
  const f=document.getElementById('qform');const fd=new FormData();['q1','q2','q3'].forEach(k=>fd.append(k,f.elements[k].value));
  fetch('/interview/reading',{method:'POST',body:fd}).then(r=>r.status===200?r.text():null).then(t=>{if(t)box.innerHTML=t}).catch(()=>{})})();
