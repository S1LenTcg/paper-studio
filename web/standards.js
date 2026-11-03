'use strict';
const norms={id:null,record:null,version:0,timer:null,submitting:false,dirty:false,request:null,filter:'issues'};
const normsTerminal=new Set(['verified','review_required','failed','cancelled','uncertain','interrupted']);
const normsNames={queued:'排队中',opening:'打开秀米',waiting_login:'等待登录',inspecting:'检查原稿',awaiting_review:'等待确认',copying:'建立副本',correcting:'修正格式',saving:'保存副本',verifying:'回读核对',verified:'回读通过',review_required:'需要核对',failed:'未完成',cancelled:'已取消',uncertain:'结果不确定',interrupted:'已中断'};
function normMessage(text,error=false){$('#standards-status').textContent=text;$('#standards-status').classList.toggle('standards-error',error);}
function normStored(id){try{if(id)localStorage.setItem('paper-studio.standards-job',id);return localStorage.getItem('paper-studio.standards-job');}catch(_){return norms.id;}}
function normLink(value){return safeXiumiURL(value);}
function normControls(record){
  const live=record&&!normsTerminal.has(record.stage),review=record?.stage==='awaiting_review'&&!record.planSubmitted;
  for(const id of ['#standards-start','#standards-url','#standards-remember'])$(id).disabled=!!live||norms.submitting;
  $('#standards-start').textContent=live?'检查中…':'开始检查';
  $('#standards-cancel').hidden=!live||!['queued','opening','waiting_login','inspecting','awaiting_review'].includes(record.stage)||!!record.planSubmitted;
  for(const control of $$('#standards-report select,#standards-report button,#standards-report input'))control.disabled=!review||norms.submitting;
  for(const control of $$('[data-norm-filter]'))control.disabled=false;
  $('#standards-apply').disabled=!review||norms.submitting||norms.dirty||!$$('.standards-issue input:checked').length;
  const link=normLink(record?.draftUrl);$('#standards-copy').hidden=!link;if(link)$('#standards-copy').href=link;
}
async function openStandards(){
  $('#standards-dialog').showModal();
  try{await normHistory();if(!norms.id&&normStored())await watchNorm(normStored());}catch(e){normMessage(e.message,true);}
}
async function normHistory(){
  const data=await api('/api/standards'),list=$('#standards-history-list');list.replaceChildren();
  for(const record of data.jobs.slice(0,8)){
    const b=document.createElement('button');b.className='standards-history-item';
    const title=document.createElement('span');title.textContent=record.title;
    const small=document.createElement('small');small.textContent=(normsNames[record.stage]||record.stage)+' · '+new Date(record.createdAt).toLocaleString('zh-CN',{hour12:false});
    b.append(title,small);b.onclick=()=>watchNorm(record.id).catch(e=>normMessage(e.message,true));list.append(b);
  }
  if(!data.bridge.available)normMessage('本地秀米适配器尚未就绪，请检查依赖。',true);
  return data;
}
async function startNorm(event){
  event.preventDefault();if(norms.submitting||norms.record&&!normsTerminal.has(norms.record.stage))return;
  const url=normLink($('#standards-url').value.trim());if(!url){normMessage('请填写秀米草稿的编辑链接。',true);return;}
  norms.submitting=true;normControls(norms.record);
  try{
    // Keep this request across a lost response. No automatic second process.
    const payload={url,requestId:crypto.randomUUID().replaceAll('-',''),options:{rememberLogin:$('#standards-remember').checked,leaveOpen:true}};
    norms.request=payload;normStored(payload.requestId);
    const record=await api('/api/standards/jobs',payload);norms.submitting=false;await watchNorm(record.id);await normHistory();
  }catch(e){norms.submitting=false;normMessage('检查请求未确认：'+e.message+'。请先刷新记录，不会自动重发。',true);$('#standards-start').disabled=true;}
}
async function watchNorm(id){
  if(norms.id&&norms.id!==id&&norms.record&&!normsTerminal.has(norms.record.stage))throw Error('请先完成或结束当前检查，再查看其他记录。');
  clearTimeout(norms.timer);norms.id=id;normStored(id);norms.dirty=false;await pollNorm(++norms.version);
}
async function pollNorm(version){
  try{
    const record=await api('/api/standards/jobs/'+norms.id);if(version!==norms.version)return;
    const prior=norms.record;norms.record=record;
    normMessage((normsNames[record.stage]||record.stage)+'\n'+record.message,['failed','uncertain','interrupted'].includes(record.stage));
    if(record.report&&$('#standards-report').dataset.loaded!==record.id)renderNormReport(record.report);
    if(record.verification){normMessage(record.message+'\n'+Object.entries(record.verification).map(([key,ok])=>({textAndImages:'文字与图片',title:'副本标题',selectedFormats:'所选格式',imagesLoaded:'图片加载'}[key]||key)+'：'+(ok?'通过':'需检查')).join(' · '));}
    normControls(record);
    if(!normsTerminal.has(record.stage))norms.timer=setTimeout(()=>pollNorm(version),1800);
  }catch(e){if(version===norms.version){normMessage('读取任务失败：'+e.message+'。可刷新记录恢复，不会重复发送。',true);norms.timer=setTimeout(()=>pollNorm(version),5000);}}
}
function renderNormReport(report){
  $('#standards-empty').hidden=true;$('#standards-report').hidden=false;$('#standards-report').dataset.loaded=norms.id;norms.dirty=false;
  $('#standards-article-title').textContent=report.title;$('#standards-body-color').value=report.bodyColor;
  $('#standards-summary').textContent=`${report.summary.automatic} 项可修正 · ${report.summary.manual} 项需复核${report.summary.unknown?' · '+report.summary.unknown+' 处待分类':''}`;
  const manual=report.issues.filter(i=>!i.automatic),groups=new Map(),manualList=$('#standards-manual-list');manualList.replaceChildren();
  for(const issue of manual){if(!groups.has(issue.field))groups.set(issue.field,{message:issue.message,ids:[]});groups.get(issue.field).ids.push(Number(issue.cellId)+1);}
  $('#standards-manual').hidden=!manual.length;$('#standards-manual-title').textContent=`人工复核（${manual.length} 项）`;
  for(const group of groups.values()){const li=document.createElement('li');li.textContent=group.message.replace('（避免重复加边距）','')+' · 区块 '+group.ids.join('、');manualList.append(li);}
  const container=$('#standards-blocks');container.replaceChildren();
  for(const cell of report.cells){
    if(!cell.text.trim())continue;
    const row=document.createElement('section');row.className='standards-block';row.dataset.cell=cell.id;
    const head=document.createElement('div');head.className='standards-block-head';
    const num=document.createElement('strong');num.textContent='区块 '+(Number(cell.id)+1);
    const select=document.createElement('select');select.setAttribute('aria-label','区块'+(Number(cell.id)+1)+'用途');select.dataset.role=cell.id;
    for(const [value,name]of Object.entries({body:'正文',footer:'小尾巴 / 署名',heading:'标题 / 装饰',caption:'图注',unknown:'待确认',ignore:'忽略'})){const option=new Option(name,value);select.add(option);}select.value=cell.role;
    select.onchange=()=>{norms.dirty=true;normControls(norms.record);normMessage('分类已修改，请重新检查。');};
    head.append(num,select);const text=document.createElement('p');text.className='standards-block-text';text.textContent=cell.text;row.append(head,text);
    const issues=report.issues.filter(i=>i.cellId===cell.id);
    row.dataset.pending=String(cell.role==='unknown'||issues.some(i=>i.automatic||i.field!=='margins'));
    for(const issue of issues){
      if(issue.field==='margins')continue;
      const label=document.createElement(issue.automatic?'label':'div');label.className='standards-issue'+(issue.automatic?'':' manual');
      if(issue.automatic){const check=document.createElement('input');check.type='checkbox';check.checked=true;check.dataset.issue=issue.id;check.onchange=()=>normControls(norms.record);label.append(check);}
      const content=document.createElement('span');content.textContent=(issue.automatic?'':'人工复核 · ')+issue.message;
      const details=document.createElement('small');const current=Array.isArray(issue.current)?issue.current.join(' / '):String(issue.current);details.textContent=`当前：${current} → 要求：${issue.target}`;content.append(details);label.append(content);row.append(label);
    }
    if(!issues.length){const p=document.createElement('p');p.className='standards-pass';p.textContent=['body','footer'].includes(cell.role)?'格式符合规范':cell.role==='unknown'?'请确认区块用途':'保留原样';row.append(p);}
    container.append(row);
  }
  $('#standards-select-all').checked=true;
  filterNormBlocks(norms.filter);
}
function filterNormBlocks(mode){
  norms.filter=mode;
  for(const row of $$('#standards-blocks .standards-block'))row.hidden=mode==='issues'&&row.dataset.pending!=='true';
  for(const button of $$('[data-norm-filter]'))button.setAttribute('aria-pressed',String(button.dataset.normFilter===mode));
  $('#standards-no-issues').hidden=mode!=='issues'||$$('#standards-blocks .standards-block').some(row=>!row.hidden);
}
function normRoles(){const result=Object.fromEntries(norms.record.report.cells.map(c=>[c.id,c.role]));for(const select of $$('[data-role]'))result[select.dataset.role]=select.value;return result;}
async function recheckNorm(){
  try{norms.submitting=true;normControls(norms.record);const report=await api('/api/standards/jobs/'+norms.id+'/report',{roles:normRoles(),bodyColor:$('#standards-body-color').value});norms.record.report=report;renderNormReport(report);normMessage('已按当前分类重新检查，请核对所选修正项。');}catch(e){normMessage(e.message,true);}finally{norms.submitting=false;normControls(norms.record);}
}
async function applyNorm(){
  if(norms.submitting||norms.dirty)return;
  norms.submitting=true;normControls(norms.record);
  try{const plan={fingerprint:norms.record.report.fingerprint,roles:normRoles(),selected:$$('.standards-issue input:checked').map(i=>i.dataset.issue),bodyColor:$('#standards-body-color').value};const record=await api('/api/standards/jobs/'+norms.id+'/apply',plan);norms.record=record;norms.submitting=false;normControls(record);await pollNorm(++norms.version);}catch(e){norms.submitting=false;normMessage('修正请求未确认：'+e.message+'。请刷新记录查看结果。',true);$('#standards-apply').disabled=true;}
}
document.addEventListener('DOMContentLoaded',()=>{
  for(const button of $$('[data-norm-filter]'))button.onclick=()=>filterNormBlocks(button.dataset.normFilter);
  $('#nav-standards').onclick=openStandards;$('#home-standards').onclick=openStandards;$('#standards-form').onsubmit=startNorm;
  $('#standards-refresh').onclick=async()=>{try{const data=await normHistory();const id=normStored();if(id&&data.jobs.some(j=>j.id===id))await watchNorm(id);else if(!norms.record){normControls(null);normMessage('未找到进行中的检查记录，可以重新发起检查。');}}catch(e){normMessage(e.message,true);}};
  $('#standards-recheck').onclick=recheckNorm;$('#standards-apply').onclick=applyNorm;
  $('#standards-body-color').onchange=()=>{norms.dirty=true;normControls(norms.record);};
  $('#standards-select-all').onchange=e=>{$$('.standards-issue input').forEach(i=>i.checked=e.target.checked);normControls(norms.record);};
  $('#standards-cancel').onclick=async()=>{try{await api('/api/standards/jobs/'+norms.id+'/cancel',{});normMessage('已请求结束本次检查，等待窗口任务退出。');}catch(e){normMessage(e.message,true);}};
});
