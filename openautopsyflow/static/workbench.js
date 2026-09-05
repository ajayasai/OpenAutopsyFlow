'use strict';
// No browser storage of case data, no remote assets, no automatic review actions.
const W={user:null,data:null,page:1,q:'',request:0,urls:[],notes:new Map(),historyPage:1};
const $w=s=>document.querySelector(s);
const ew=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const enc=encodeURIComponent;
const badge=(text,kind='')=>`<span class="wb-badge ${ew(kind)}">${ew(text)}</span>`;
const prose=value=>`<pre class="wb-prose">${ew(typeof value==='string'?value:JSON.stringify(value,null,2))}</pre>`;
const button=(label,action,extra='')=>`<button type="button" data-wb="${action}" ${extra}>${label}</button>`;
function error(e){$w('#wb-error').hidden=false;$w('#wb-error').textContent=e.message||String(e);}
function clearError(){$w('#wb-error').hidden=true;}
async function call(path,options={},raw=false){
  const headers={...options.headers};
  if(W.user?.csrf)headers['X-CSRF-Token']=W.user.csrf;
  if(options.body){headers['Content-Type']='application/json';options.body=JSON.stringify(options.body);}
  const r=await fetch('/api'+path,{...options,headers,credentials:'same-origin',cache:'no-store'});
  if(!r.ok){let d;try{d=await r.json();}catch{d={detail:r.statusText};}
    if(r.status===401){W.user=null;W.data=null;W.notes.clear();releaseUrls();$w('#wb-report').replaceChildren();$w('#wb-picker').innerHTML='<p>Your session ended. <a href="/">Sign in to casework</a>, then reopen the workbench.</p>';}
    throw new Error(typeof d.detail==='string'?d.detail:d.detail?.message||JSON.stringify(d.detail));}
  return raw?r:r.json();
}
function releaseUrls(){for(const url of W.urls)URL.revokeObjectURL(url);W.urls=[];}
function saveBlob(blob,name){const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
async function cases(){
  const response=await call('/cases?'+new URLSearchParams({q:W.q,page:W.page}));
  $w('#wb-picker').innerHTML=`<section class="wb-card"><div class="wb-bar"><h2>Your assigned cases</h2><span class="wb-meta">${response.total} matching · Page ${W.page}</span></div><form id="wb-search" class="wb-search"><label for="wb-query">Search</label><input id="wb-query" maxlength="200" value="${ew(W.q)}" placeholder="Case reference or authority"><button type="submit">Search cases</button></form><div class="wb-actions">${W.page>1?button('Previous page','previous'):''}${W.page*50<response.total?button('Next page','next'):''}</div><div class="wb-table-list">${response.items.map(c=>`<div class="wb-bar wb-source"><span><strong>${ew(c.case_no)}</strong> ${badge(c.status)} <span class="wb-meta">${ew(c.examiner)}</span></span>${button('Choose report →','case',`data-id="${ew(c.id)}"`)}</div>`).join('')||'<p>No assigned matching cases.</p>'}</div></section>`;
  $w('#wb-search').onsubmit=async e=>{e.preventDefault();W.q=$w('#wb-query').value;W.page=1;try{await cases();}catch(err){error(err);}};
}
async function chooseCase(id){
  const c=await call('/cases/'+enc(id));
  $w('#wb-report').innerHTML=`<section class="wb-card"><h2>${ew(c.data.case_no)} · Reports</h2>${c.reports.map(r=>`<p>${button(`Report ${r.number} · ${ew(r.status)} →`,'report',`data-id="${ew(r.id)}"`)}</p>`).join('')||`<p>No report yet. <a href="/#/case/${enc(id)}">Create a draft in casework.</a></p>`}</section>`;
  W.data=null;W.request++;releaseUrls();
}
function fields(changes){return changes.map(c=>`<div class="wb-source"><code>${ew(c.path)}</code><div class="wb-diff"><div class="wb-before"><strong>Before${c.before_present?'':' · absent'}</strong>${prose(c.before_present?c.before:'(absent)')}</div><div class="wb-after"><strong>After${c.after_present?'':' · absent'}</strong>${prose(c.after_present?c.after:'(absent)')}</div></div></div>`).join('');}
function sourceDiff(d){return `<p class="wb-meta">${d.change_count} changed source items. Case permissions or other revision changes can also require re-review.</p>${d.case.length?`<details open><summary>Case intake</summary>${fields(d.case)}</details>`:''}${['records','evidence','custody'].map(group=>d[group].map(c=>`<details><summary>${ew(group)} · ${ew(c.label)} · ${ew(c.change)}</summary>${fields(c.fields)}</details>`).join('')).join('')}`;}
function historyControls(h){
  const options=h.items.map(x=>`<option value="${x.version}">v${x.version} · ${ew(x.status)}${x.capture_kind==='legacy_baseline'?' · earliest retained baseline':''}</option>`).join('');
  return `<p class="wb-meta">${h.total} captured versions · page ${h.page}. A legacy baseline does not reconstruct earlier drafts.</p><div class="wb-actions"><label for="wb-from">From</label><select id="wb-from">${options}</select><label for="wb-to">To</label><select id="wb-to">${options}</select>${button('Compare versions','compare')}${button('View selected original revision','revision')}${h.page>1?button('Newer history','history-newer'):''}${h.page*50<h.total?button('Older history','history-older'):''}</div><div id="wb-comparison"></div>`;
}
function noteKey(e){return `${W.data.report.id}:${W.data.report.version}:${e.id}`;}
function render(){
  releaseUrls();const d=W.data,r=d.report;const stale=r.source_revision!==d.current_case_revision;
  const able=d.independent_reviewer&&r.status==='in_review'&&!stale;
  const blocked=d.checks.issues.some(i=>i.severity==='blocker')||d.comments.some(c=>c.blocking&&!c.resolved_at);
  const actions=`<a class="wb-button" href="/#/case/${enc(r.case_id)}">Open case & report editor</a>${button('Reload workbench','reload')}${button('Save audit checkpoint','checkpoint')}${d.role==='examiner'&&r.status==='draft'&&stale?button('Refresh source snapshot','refresh'):''}${d.independent_reviewer&&r.status==='in_review'?button('Approve this version','approve',`class="wb-primary" ${blocked||d.review.remaining?'disabled':''}`):''}${['examiner','reviewer','coordinator'].includes(d.role)&&r.status==='approved'?button('Issue approved report','issue','class="wb-primary"'):''}`;
  const originals=d.review.required.map(e=>`<article class="wb-source" data-evidence="${ew(e.id)}"><div class="wb-bar"><h3>${ew(e.filename)}</h3>${badge(e.receipt?'Attested':d.review.legacy_issued?'No historical receipt':'Review needed',e.receipt||d.review.legacy_issued?'':'warn')}</div><p class="wb-meta">${ew(e.required_because.join('; '))}</p><code>SHA-256 ${ew(e.sha256)}</code><div class="wb-actions">${button('Open original','original',`data-id="${ew(e.id)}" ${e.scan_status!=='clean'?'disabled':''}`)}</div><div id="preview-${ew(e.id)}"></div>${e.receipt?`<p class="wb-meta">Receipt for v${e.receipt.report_version} · ${ew(e.receipt.created_at)}</p>${prose(e.receipt.statement)}`:able?`<form class="wb-attest" data-id="${ew(e.id)}"><label for="note-${ew(e.id)}">Your review note</label><textarea id="note-${ew(e.id)}" required minlength="10" maxlength="2000">${ew(W.notes.get(noteKey(e))||'')}</textarea><label class="wb-check"><input type="checkbox" required> I reviewed this original against the report. This is my attestation, not a medical correctness check.</label><button type="submit">Record evidence review</button></form>`:`<p class="wb-meta">An independent assigned reviewer records this during a current, in-review round.</p>`}</article>`).join('')||'<p class="wb-empty">No laboratory result or linked supporting original requires a receipt for this snapshot. Human report review is still required.</p>';
  $w('#wb-report').innerHTML=`<section class="wb-card"><div class="wb-bar"><div><p class="wb-eyebrow">${ew(r.snapshot.case.case_no)} / REPORT ${r.number}</p><h2>${ew(r.kind)} report · version ${r.version}</h2></div><div>${badge(r.status)}${badge(stale?'Sources changed':'Snapshot current',stale?'warn':'')}</div></div><p class="wb-meta">Frozen source revision ${r.source_revision} · current case revision ${d.current_case_revision} · your role: ${ew(d.role)}</p><div class="wb-actions">${actions}</div><p class="wb-meta">Checkpoints must be retained separately with a trusted public key to detect a later divergent or truncated audit chain.</p></section><section class="wb-card"><h2>Structural checks & review prompts</h2>${d.checks.issues.map(i=>`<p>${badge(i.severity,i.severity==='blocker'?'blocker':'warn')}${ew(i.message)}</p>`).join('')||'<p>No current structural or workflow prompts.</p>'}<p class="wb-meta">${ew(d.checks.disclaimer)}</p></section><div class="wb-grid"><section class="wb-card"><h2>Frozen narrative</h2><p class="wb-meta">Never rewritten by refreshing source records.</p>${r.sections.map(s=>`<h3>${ew(s.title)}</h3>${prose(s.text||'(empty)')}`).join('')}</section><section class="wb-card"><div class="wb-bar"><h2>Original-evidence review</h2>${badge(d.review.legacy_issued?'Legacy issued report':`${d.review.remaining} remaining`,d.review.remaining&&!d.review.legacy_issued?'warn':'')}</div><p class="wb-meta">Receipts are bound to the report digest, review round, original-file hash and reviewer account. They do not change the separate case-level evidence review flag.</p>${d.review.legacy_issued?'<p class="wb-meta">No pre-migration review receipts were captured. The existing issued report remains unchanged; no retroactive attestation is invented.</p>':''}${originals}</section></div><section class="wb-card"><h2>Finding-to-report traceability</h2><table><thead><tr><th>Report section</th><th>Reference / status</th><th>Supporting originals</th></tr></thead><tbody>${d.links.map(l=>`<tr><td>${ew(l.section_title)}</td><td>${ew(l.kind)} ${ew(l.reference)}<br>${badge(l.status,l.status!=='resolved'?'warn':'')}${l.source?.data?`<details><summary>Frozen finding</summary>${prose(l.source.data)}</details>`:''}</td><td>${l.evidence.map(e=>`${ew(e.filename)}<br><code>${ew(e.sha256)}</code>`).join('<hr>')||'No supporting original linked'}</td></tr>`).join('')||'<tr><td colspan="3">No explicit or recognized injury references. This is not a clinical-language completeness assessment.</td></tr>'}</tbody></table></section><section class="wb-card"><h2>Changes since the frozen snapshot</h2>${sourceDiff(d.source_changes)}</section><section class="wb-card"><h2>Preserved report revisions</h2><div id="wb-history">${historyControls(d.history)}</div></section><section class="wb-card"><h2>Reviewer discussion</h2>${d.comments.map(c=>`<div class="wb-comment"><strong>${ew(c.name)}</strong> ${badge(c.resolved_at?'Resolved':c.blocking?'Blocking':'Comment',c.blocking&&!c.resolved_at?'warn':'')}${prose(c.body)}${!c.resolved_at&&d.role==='reviewer'&&r.status!=='issued'?button('Resolve comment','resolve',`data-id="${ew(c.id)}"`):''}</div>`).join('')||'<p>No reviewer comments.</p>'}${['examiner','reviewer'].includes(d.role)&&r.status!=='issued'?`<form id="wb-comment-form"><label for="wb-comment-text">Comment</label><textarea id="wb-comment-text" minlength="3" maxlength="5000" required></textarea>${d.role==='reviewer'?'<label class="wb-check"><input id="wb-blocking" type="checkbox"> Blocking reviewer comment</label>':''}<button type="submit">Add comment</button></form>`:''}</section>`;
  if(d.history.items.length>1)$w('#wb-from').value=d.history.items[1].version;
  for(const form of document.querySelectorAll('.wb-attest')){
    form.querySelector('textarea').addEventListener('input',e=>W.notes.set(noteKey({id:form.dataset.id}),e.target.value));
    form.onsubmit=async event=>{event.preventDefault();const submit=form.querySelector('button');submit.disabled=true;clearError();const target=d.review.required.find(e=>e.id===form.dataset.id);
      try{await call(`/reports/${enc(r.id)}/review-receipts`,{method:'POST',body:{version:r.version,evidence_id:target.id,evidence_sha256:target.sha256,basis_digest:d.review.basis_digest,statement:form.querySelector('textarea').value,acknowledged:form.querySelector('input').checked}});W.notes.delete(noteKey(target));await loadReport(r.id);}catch(e){error(e);submit.disabled=false;}};
  }
  if($w('#wb-comment-form'))$w('#wb-comment-form').onsubmit=async event=>{event.preventDefault();try{await call(`/reports/${enc(r.id)}/comments`,{method:'POST',body:{body:$w('#wb-comment-text').value,blocking:!!$w('#wb-blocking')?.checked}});await loadReport(r.id);}catch(e){error(e);}};
}
async function loadReport(id){const ticket=++W.request;const d=await call(`/reports/${enc(id)}/workbench`);if(ticket!==W.request)return;W.data=d;W.historyPage=1;if(['http:','https:'].includes(location.protocol))history.replaceState(null,'','/review?report='+enc(id));render();}
async function dispatch(action,id){
  if(action==='case')return chooseCase(id);
  if(action==='report')return loadReport(id);
  if(action==='next'||action==='previous'){W.page+=action==='next'?1:-1;return cases();}
  const d=W.data,r=d?.report;if(!r)return;
  if(action==='reload')return loadReport(r.id);
  if(action==='checkpoint'){const res=await call(`/cases/${enc(r.case_id)}/audit-checkpoint`,{method:'POST'},true);saveBlob(await res.blob(),`audit-checkpoint-${r.case_id}.json`);return;}
  if(['refresh','approve','issue'].includes(action)){
    const question=action==='refresh'?'Refresh sources without changing the narrative? Reconcile the opinion manually afterward.':action==='approve'?'Approve this exact report version as its independent reviewer?':'Issue and freeze the approved report? Later changes require a supplement.';
    if(!confirm(question))return;
    await call(`/reports/${enc(r.id)}/actions/${action}`,{method:'POST',body:{version:r.version}});return loadReport(r.id);
  }
  if(action==='original'){
    const res=await call(`/reports/${enc(r.id)}/review-evidence/${enc(id)}`,{},true),blob=await res.blob();
    if(W.data?.report.id!==r.id||W.data.report.version!==r.version)return;
    const target=d.review.required.find(e=>e.id===id),holder=$w('#preview-'+id);holder.replaceChildren();
    const preview=document.createElement('div');preview.className='wb-preview';
    if(blob.type.startsWith('text/plain')&&blob.size<1024*1024){const text=document.createElement('pre');text.className='wb-prose';text.textContent=await blob.text();preview.append(text);}
    else if(['image/png','image/jpeg'].includes(blob.type)){const image=document.createElement('img');image.alt='Original '+target.filename;image.src=`/api/reports/${enc(r.id)}/review-evidence/${enc(id)}?inline=true`;preview.append(image);}
    else{preview.textContent='Original downloaded. Open it in your approved local viewer before attesting.';saveBlob(blob,target.filename);}
    holder.append(preview);$w('#wb-status').textContent='Original opened and hash checked. Reading and interpretation remain your responsibility.';return;
  }
  if(action==='compare'){
    const result=await call(`/reports/${enc(r.id)}/comparison?`+new URLSearchParams({from_version:$w('#wb-from').value,to_version:$w('#wb-to').value}));
    $w('#wb-comparison').innerHTML=`<p>${badge(`v${result.from_version} → v${result.to_version}`)}${ew(result.status.before)} → ${ew(result.status.after)}</p>${result.sections.map(s=>`<h3>${ew(s.key)}</h3><div class="wb-diff"><div class="wb-before"><strong>Before</strong>${prose(s.before?.text??'(absent)')}</div><div class="wb-after"><strong>After</strong>${prose(s.after?.text??'(absent)')}</div></div>`).join('')||'<p>No narrative changes.</p>'}${sourceDiff(result.sources)}${result.acknowledgements.length?`<h3>Review rationale changes</h3>${fields(result.acknowledgements)}`:''}`;return;
  }
  if(action==='revision'){const result=await call(`/reports/${enc(r.id)}/history/${enc($w('#wb-from').value)}`);$w('#wb-comparison').innerHTML=`<h3>Retained v${result.report.version} · ${ew(result.capture_kind)}</h3>${result.report.sections.map(s=>`<h3>${ew(s.title)}</h3>${prose(s.text)}`).join('')}`;return;}
  if(action.startsWith('history-')){W.historyPage+=action==='history-older'?1:-1;const h=await call(`/reports/${enc(r.id)}/history?page=${W.historyPage}`);$w('#wb-history').innerHTML=historyControls(h);return;}
  if(action==='resolve'){await call(`/reports/${enc(r.id)}/comments/${enc(id)}/resolve`,{method:'POST'});return loadReport(r.id);}
}
document.addEventListener('click',async event=>{const b=event.target.closest('[data-wb]');if(!b||b.disabled)return;b.disabled=true;clearError();try{await dispatch(b.dataset.wb,b.dataset.id);}catch(e){error(e);}finally{if(b.isConnected)b.disabled=false;}});
(async()=>{try{W.user=await call('/me');await cases();const id=new URLSearchParams(location.search).get('report');if(id)await loadReport(id);}catch(e){error(e);}})();
