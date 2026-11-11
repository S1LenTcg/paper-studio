'use strict';

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
// Copy mutable containers; immutable image strings can be shared by undo snapshots.
function clone(value) {
  if (Array.isArray(value)) return value.map(clone);
  if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value).map(([key,item]) => [key,clone(item)]));
  return value;
}
const labels = {heading:'小标题', paragraph:'正文', quote:'引用', image:'图片', divider:'分隔', signature:'署名'};
let bootstrap, session, selected = 0, history = [], renderTimer, saveTimer, toastTimer;
let renderVersion = 0, saveChain = Promise.resolve(), activeJob = null, jobTimer;
let previewResetRequested = false, articleResizeObserver = null;
let viewedXiumiJob = null, xiumiVersion = 0, xiumiSending = false, xiumiRequest = null, xiumiRetryAllowed = false;
let draftCache = [];

async function api(path, data) {
  const options = data === undefined ? {} : {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(data)};
  const response = await fetch(path, options);
  const value = await response.json();
  if (!response.ok) throw new Error(value.error || '操作未完成，请重试');
  return value;
}

function toast(message, error=false) {
  clearTimeout(toastTimer);
  $('#toast').textContent = message;
  $('#toast').classList.toggle('error', error);
  $('#toast').hidden = false;
  toastTimer = setTimeout(() => { $('#toast').hidden=true; }, error ? 6500 : 3200);
}

function documentValue() {
  const doc = clone(session.doc);
  doc.title = doc.title.trim() || '未命名推文';
  return doc;
}

function remember() {
  history.push(clone(session.doc));
  if (history.length > 30) history.shift();
  $('#undo-button').disabled = false;
}

function changed() {
  session.revision = (session.revision || 0) + 1;
  $('#save-state').textContent = '正在编辑…';
  clearTimeout(renderTimer);
  renderTimer = setTimeout(updatePreview, 160);
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => saveDraft(false).catch(error => toast(error.message,true)), 1000);
  updateCounters();
}

function hasUnsavedChanges(owner=session) {
  return !!owner && (owner.revision || 0) > (owner.savedRevision || 0);
}

function bindSaveLifecycle() {
  window.addEventListener('beforeunload', event => {
    if (!hasUnsavedChanges()) return;
    event.preventDefault();
    event.returnValue = '';
  });
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'hidden' && hasUnsavedChanges()) {
      saveDraft(false).catch(error => toast(error.message,true));
    }
  });
}

function newDraftId() {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, byte => byte.toString(16).padStart(2,'0')).join('');
}

async function saveDraft(notify=true) {
  clearTimeout(saveTimer);
  const owner = session;
  const revision = owner.revision || 0;
  const doc = documentValue();
  // Keep the id even when the response is lost, so retry updates the same draft.
  if (!owner.id) owner.id = newDraftId();
  $('#save-state').textContent = '正在保存…';
  saveChain = saveChain.catch(() => {}).then(async () => {
    try {
      const record = await api('/api/drafts', {id:owner.id, document:doc});
      owner.id = record.id;
      owner.savedRevision = Math.max(owner.savedRevision || 0, revision);
      if (session === owner) {
        $('#save-state').textContent = hasUnsavedChanges(owner) ? '正在编辑…' : '已保存到本机';
        try { localStorage.setItem('paper-studio.last-draft', record.id); } catch (_) { /* Server remains the source of truth. */ }
      }
      return record;
    } catch (error) {
      if (session === owner) $('#save-state').textContent = '未保存 · 请重试保存';
      throw error;
    }
  });
  const result = await saveChain;
  if (notify) toast('草稿已保存到这台电脑');
  try { await refreshDrafts(); }
  catch (_) { toast('稿件已保存，但草稿列表刷新失败，请稍后重新打开列表',true); }
  return result;
}

function showView(name) {
  for (const view of ['home','editor','drafts']) {
    $(`#${view}-view`).hidden = view !== name;
    $(`#nav-${view}`).classList.toggle('active',view===name);
    if(view===name)$(`#nav-${view}`).setAttribute('aria-current','page');
    else $(`#nav-${view}`).removeAttribute('aria-current');
  }
  const names = {home:'创作首页', editor:'排版工作台', drafts:'我的草稿'};
  $('#breadcrumb').textContent = `工作空间 / ${names[name]}`;
  if (name === 'editor') updatePreview();
  if (name === 'home' || name === 'drafts') refreshDrafts().catch(e => toast(e.message,true));
  window.scrollTo({top:0});
}

function updateCounters() {
  const text = [session.doc.title, session.doc.subtitle, ...session.doc.blocks.map(b=>b.text)].join('');
  $('#word-count').textContent = `${text.replace(/\s/g,'').length} 字`;
  $('#block-count').textContent = String(session.doc.blocks.length);
}

function fillFields() {
  for (const key of ['title','subtitle','author','column']) $(`#${key}-input`).value = session.doc[key] || '';
  $('#font-input').value = session.doc.fontSize;
  $('#line-input').value = session.doc.lineHeight;
  $('#font-value').textContent = `${session.doc.fontSize} px`;
  $('#line-value').textContent = Number(session.doc.lineHeight).toFixed(2);
  $$('.theme-choice').forEach(button => button.setAttribute('aria-pressed', String(!session.doc.referenceStyle && button.dataset.theme===session.doc.theme)));
  const theme=bootstrap.themes[session.doc.theme];
  $('#template-description').textContent=session.doc.referenceStyle?`参考：${session.doc.referenceStyle.title}`:session.doc.aiLayout?`AI · ${theme.name}`:theme.name;
  $('#reference-current').hidden=!session.doc.referenceStyle;
  $('#reference-current-title').textContent=session.doc.referenceStyle?.title||'';
  $('#spacing-setting').hidden=!!session.doc.referenceStyle||!session.doc.theme.startsWith('doocs-');
  $('#spacing-input').value=session.doc.blockSpacing??.65;
  $('#spacing-value').textContent=Number(session.doc.blockSpacing??.65).toFixed(2);
  const legacy=$('#legacy-themes');if(legacy)legacy.open=!session.doc.referenceStyle&&!session.doc.theme.startsWith('doocs-');
  renderBlocks(); updateCounters();
}

async function updatePreview() {
  const version = ++renderVersion;
  try {
    const result = await api('/api/render', {document:documentValue(),preview:true});
    if (version !== renderVersion) return;
    const frame = $('#article-preview');
    frame.srcdoc = '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><style>html,body{overflow:hidden}body{margin:0}img{max-width:100%}*{box-sizing:border-box}</style></head><body>' + result.html + '</body></html>';
    frame.onload = () => {
      articleResizeObserver?.disconnect();
      const body=frame.contentDocument?.body;
      if(body){
        const fit=()=>{const height=Math.max(500,Math.ceil(body.getBoundingClientRect().height)+2);if(frame.style.height!==height+'px')frame.style.height=height+'px';};
        fit();articleResizeObserver=new ResizeObserver(fit);articleResizeObserver.observe(body);
      }
      if (previewResetRequested) { $('.preview-scroll').scrollTop=0; previewResetRequested=false; }
    };
  } catch (error) { toast(error.message,true); }
}

function renderBlocks() {
  const list = $('#block-list'); list.replaceChildren();
  session.doc.blocks.forEach((block,index) => {
    const button = document.createElement('button');
    button.className = 'block-item' + (selected===index?' active':'');
    button.setAttribute('aria-pressed',String(selected===index));
    const number = document.createElement('span'); number.textContent = String(index+1).padStart(2,'0');
    const details = document.createElement('div');
    const title = document.createElement('b'); title.textContent = block.text.split('\n')[0] || labels[block.type];
    const type = document.createElement('small'); type.textContent = labels[block.type];
    details.append(title,type); button.append(number,details);
    button.addEventListener('click', () => { selected=index; renderBlocks(); });
    list.append(button);
  });
  renderBlockEditor(); updateCounters();
}

function renderBlockEditor() {
  const container = $('#block-editor'); container.replaceChildren();
  const block = session.doc.blocks[selected];
  if (!block) return;
  const top = document.createElement('div'); top.className='block-editor-title';
  const label = document.createElement('span'); label.textContent=`编辑${labels[block.type]}`;
  const actions = document.createElement('div'); actions.className='block-actions';
  for (const [title, symbol, handler, disabled] of [
    ['上移区块','↑',()=>moveBlock(-1),selected===0],
    ['下移区块','↓',()=>moveBlock(1),selected===session.doc.blocks.length-1],
    ['移除区块','×',()=>{if(session.doc.blocks.length===1){toast('至少保留一个区块');return;}remember();session.doc.blocks.splice(selected,1);selected=Math.min(selected,session.doc.blocks.length-1);renderBlocks();changed();},false],
  ]) {
    const b=document.createElement('button'); b.textContent=symbol; b.title=title; b.setAttribute('aria-label',title); b.disabled=disabled; b.onclick=handler; actions.append(b);
  }
  top.append(label,actions);container.append(top);
  if (block.type==='image') {
    const img=document.createElement('img'); img.src=block.src; img.alt=block.text||'文稿配图'; container.append(img);
    const input=document.createElement('input'); input.type='file';input.accept='image/png,image/jpeg,image/webp';input.hidden=true;
    const replace=document.createElement('button');replace.className='secondary';replace.textContent='选择本地图片 / 替换';replace.onclick=()=>input.click();
    input.onchange=async()=>{
      const file=input.files[0]; if(!file)return;
      if(file.size>8000000){toast('图片请控制在 8 MB 以内',true);return;}
      if(!['image/png','image/jpeg','image/webp'].includes(file.type)){toast('支持 PNG、JPEG、WebP 图片',true);return;}
      const owner=session;
      const reader=new FileReader();reader.onload=()=>{if(session!==owner || !owner.doc.blocks.includes(block))return;remember();block.src=reader.result;renderBlockEditor();changed();};reader.readAsDataURL(file);
    };
    container.append(replace,input);
  }
  if(block.type!=='divider') {
    const textarea=document.createElement('textarea');textarea.value=block.text;textarea.maxLength=20000;textarea.setAttribute('aria-label',block.type==='image'?'图片说明':'区块内容');textarea.placeholder=block.type==='image'?'为这张图片写一句说明…':'在这里写下内容…';
    textarea.addEventListener('input',()=>{remember();block.text=textarea.value;const item=$$('#block-list .block-item b')[selected];if(item)item.textContent=block.text.split('\n')[0]||labels[block.type];changed();});container.append(textarea);
  } else {const p=document.createElement('p');p.className='muted';p.textContent='分隔线。可以移动，也可以删除。';container.append(p);}
}

function moveBlock(direction) {
  const next=selected+direction;if(next<0||next>=session.doc.blocks.length)return;
  remember();[session.doc.blocks[selected],session.doc.blocks[next]]=[session.doc.blocks[next],session.doc.blocks[selected]];selected=next;renderBlocks();changed();
}

function buildThemes() {
  const legacy=document.createElement('details');legacy.id='legacy-themes';
  const summary=document.createElement('summary');summary.textContent='之前的版式';legacy.append(summary);
  for(const [id,theme] of Object.entries(bootstrap.themes)) {
    const b=document.createElement('button');b.className='theme-choice';b.dataset.theme=id;b.setAttribute('aria-pressed','false');
    b.style.setProperty('--theme-color',theme.accent);b.style.setProperty('--theme-paper',theme.paper);
    const specimen=document.createElement('span');specimen.className=`theme-specimen specimen-${id}`;specimen.setAttribute('aria-hidden','true');
    for(const kind of ['title','line','photo','line','line']){const part=document.createElement('i');part.className=`specimen-${kind}`;specimen.append(part);}
    const details=document.createElement('span');details.className='theme-details';const name=document.createElement('strong');name.textContent=theme.name;const note=document.createElement('small');note.textContent=theme.description;details.append(name,note);
    b.append(specimen,details);b.onclick=()=>selectTheme(id);(theme.source?$('#themes'):legacy).append(b);
  }
  $('#themes').append(legacy);
}

function selectTheme(id) {
  if(session.doc.theme===id && !session.doc.referenceStyle && !session.doc.aiLayout)return;
  remember();session.doc.theme=id;delete session.doc.referenceStyle;delete session.doc.aiLayout;for(const block of session.doc.blocks)delete block.appearance;previewResetRequested=true;fillFields();changed();
}

async function compareThemes() {
  const dialog=$('#comparison-dialog'),grid=$('#comparison-grid');grid.replaceChildren();dialog.showModal();
  const source=documentValue();
  await Promise.all(Object.entries(bootstrap.themes).map(async([id,theme])=>{
    const card=document.createElement('section');card.className='comparison-card';
    const heading=document.createElement('h3');heading.textContent=theme.name;
    const note=document.createElement('p');note.className='comparison-note';note.textContent=theme.description;
    const scroll=document.createElement('div');scroll.className='comparison-preview';
    const page=document.createElement('div');page.className='comparison-page';
    const frame=document.createElement('iframe');frame.title=theme.name+'版式预览';frame.setAttribute('sandbox','allow-same-origin');page.append(frame);scroll.append(page);
    const choose=document.createElement('button');choose.className='secondary full';choose.textContent='使用'+theme.name;choose.onclick=()=>{selectTheme(id);dialog.close();};
    card.append(heading,note,scroll,choose);grid.append(card);
    try{const result=await api('/api/render',{document:{...source,theme:id,referenceStyle:null,aiLayout:null},preview:true});
      const fit=()=>{const scale=scroll.clientWidth/390;const height=frame.contentDocument?.body?.scrollHeight||1600;frame.style.height=height+'px';frame.style.transform=`scale(${scale})`;page.style.height=(height*scale)+'px';};
      frame.onload=()=>{fit();scroll.scrollTop=0;const observer=new ResizeObserver(fit);observer.observe(scroll);dialog.addEventListener('close',()=>observer.disconnect(),{once:true});};
      frame.srcdoc='<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><style>body{margin:0}*{box-sizing:border-box}img{max-width:100%}</style></head><body>'+result.html+'</body></html>';
    }catch(error){note.textContent='预览未能载入：'+error.message;}
  }));
}

async function refreshDrafts() {
  draftCache=await api('/api/drafts'); $('#draft-count').textContent=String(draftCache.length);
  renderDrafts();
}

function renderDrafts() {
  const query=$('#draft-search').value.trim().toLocaleLowerCase();
  const filtered=draftCache.filter(d=>d.title.toLocaleLowerCase().includes(query));
  $('#draft-total').textContent=query?`${filtered.length} / ${draftCache.length}`:`${draftCache.length} 篇`;
  for(const [selector,items] of [['#recent-drafts',draftCache.slice(0,3)],['#all-draft-list',filtered]]) {
    const container=$(selector);container.replaceChildren();
    if(!items.length){const p=document.createElement('p');p.className='empty-draft';p.textContent=query&&selector==='#all-draft-list'?'没有找到这篇稿，换个关键词试试。':'还没有草稿。新建一篇，或先打开示例看看。';container.append(p);continue;}
    for(const draft of items){const row=document.createElement('button');row.className='draft-row';const icon=document.createElement('span');icon.className='draft-icon';icon.textContent='▤';const detail=document.createElement('div');const title=document.createElement('b');title.textContent=draft.title.replace(/\n/g,' ');const meta=document.createElement('small');meta.textContent=`${draft.reference?'参考排版':(bootstrap.themes[draft.theme]?.name||'排版文稿')} · ${new Date(draft.updatedAt).toLocaleString('zh-CN',{hour12:false})}`;detail.append(title,meta);const arrow=document.createElement('span');arrow.textContent='↗';row.append(icon,detail,arrow);row.onclick=()=>loadDraft(draft.id).catch(e=>toast(e.message,true));container.append(row);}
  }
}

async function loadDraft(id) {
  clearTimeout(saveTimer);if((session.revision||0)>(session.savedRevision||0))await saveDraft(false);else await saveChain.catch(()=>{});
  const record=await api(`/api/drafts/${id}`);session={id:record.id,doc:record.document};history=[];selected=0;previewResetRequested=true;fillFields();$('#save-state').textContent='已保存到本机';showView('editor');
}

async function newDraft() {
  clearTimeout(saveTimer);if((session.revision||0)>(session.savedRevision||0))await saveDraft(false);else await saveChain.catch(()=>{});
  session={id:null,doc:clone(bootstrap.sample)};history=[];selected=0;previewResetRequested=true;fillFields();$('#save-state').textContent='示例文稿 · 可直接修改';showView('editor');
}

async function newBlankDraft() {
  clearTimeout(saveTimer);if((session.revision||0)>(session.savedRevision||0))await saveDraft(false);else await saveChain.catch(()=>{});
  const doc=clone(bootstrap.sample);
  Object.assign(doc,{title:'',subtitle:'',author:'',column:'',blocks:[{type:'paragraph',text:''}]});
  delete doc.referenceStyle;delete doc.aiLayout;
  session={id:null,doc};history=[];selected=0;previewResetRequested=true;
  $('.editor-grid').dataset.panel='blocks';$('.editor-grid').dataset.workspacePane='edit';
  for(const button of $$('.editor-panel-tabs button'))button.setAttribute('aria-pressed',String(button.dataset.editorPanel==='blocks'));
  for(const button of $$('.workspace-switch button'))button.setAttribute('aria-pressed',String(button.dataset.workspacePane==='edit'));
  fillFields();$('#save-state').textContent='新文章';showView('editor');
  $('#title-input').focus();
}

function showInfo(title,paragraphs) {
  $('#info-title').textContent=title;const content=$('#info-content');content.replaceChildren();
  for(const text of paragraphs){const p=document.createElement('p');p.textContent=text;content.append(p);}
  $('#info-dialog').showModal();
}

function storedXiumiRequest(value) {
  if(value!==undefined)xiumiRequest=value;
  try{if(value!==undefined){if(value)localStorage.setItem('paper-studio.xiumi-request',value);else localStorage.removeItem('paper-studio.xiumi-request');}else xiumiRequest=localStorage.getItem('paper-studio.xiumi-request');}catch(_){/* In-memory id still prevents duplicate clicks. */}
  return xiumiRequest;
}

async function openExport() {
  $('#bridge-message').textContent=bootstrap.bridge.available?'首次使用需在新窗口登录。':bootstrap.bridge.message;
  $('#send-xiumi').disabled=!bootstrap.bridge.available||!!activeJob||(!!viewedXiumiJob&&!xiumiRetryAllowed)||xiumiSending;
  $('#export-dialog').showModal();
  await loadXiumiJobs(!viewedXiumiJob&&!activeJob);
}

const xiumiStageNames={queued:'排队中',opening:'打开窗口',waiting_login:'等待登录',writing:'写入与上传',preflight:'保存前检查',saving:'保存中',verifying:'重新打开核对',verified:'自动核对通过',review_required:'已保存，需核对',uncertain:'保存结果不确定',failed:'未完成',cancelled:'已取消',interrupted:'已中断，需检查'};
function safeXiumiURL(value){
  try{const url=new URL(value);return url.protocol==='https:'&&url.host==='xiumi.us'&&!url.username&&!url.password&&url.pathname.replace(/\/$/,'')==='/studio/v5'&&/^\/paper\/for\/[1-9]\d*(?:\/cube\/\d+)?\/?$/.test(url.hash.slice(1))?url.href:null;}catch(_){return null;}
}

async function loadXiumiJobs(restore=false) {
  try{
    const records=await api('/api/xiumi/jobs'),list=$('#xiumi-history-list');list.replaceChildren();
    $('#xiumi-history').hidden=!records.length;
    for(const record of records.slice(0,8)){
      const button=document.createElement('button');button.className='xiumi-history-item';
      const title=document.createElement('strong');title.textContent=record.title||'之前的发送任务';
      const meta=document.createElement('small');meta.textContent=`${xiumiStageNames[record.stage]||record.stage} · ${record.createdAt?new Date(record.createdAt).toLocaleString('zh-CN',{hour12:false}):''}`;
      button.append(title,meta);button.disabled=!!activeJob||xiumiSending;button.onclick=()=>watchXiumiJob(record.id);list.append(button);
    }
    if(restore){
      const pending=storedXiumiRequest(),record=records.find(r=>r.id===pending)||records.find(r=>!terminal.has(r.stage))||records[0];
      if(record)await watchXiumiJob(record.id);
      else if(pending){$('#job-progress').hidden=false;$('#job-message').textContent='上次请求未在记录中找到。请先刷新记录并查看秀米；不会自动重发。';$('#send-xiumi').disabled=true;$('#xiumi-new-task').hidden=false;}
    }
    return records;
  }catch(error){$('#bridge-message').textContent='发送记录暂时读取失败，请刷新记录后再发送：'+error.message;$('#send-xiumi').disabled=true;$('#xiumi-new-task').hidden=false;}
}

async function watchXiumiJob(id){
  if(activeJob&&activeJob!==id)return;
  clearTimeout(jobTimer);activeJob=id;viewedXiumiJob=id;
  xiumiRetryAllowed=false;
  $('#send-xiumi').disabled=true;$('#xiumi-new-task').hidden=true;
  await pollJob(++xiumiVersion);
}

async function sendXiumi(){
  if(xiumiSending||activeJob||(viewedXiumiJob&&!xiumiRetryAllowed))return;
  xiumiSending=true;$('#send-xiumi').disabled=true;
  let requestId=null;
  try{
    if(viewedXiumiJob&&xiumiRetryAllowed){
      viewedXiumiJob=null;xiumiRetryAllowed=false;storedXiumiRequest(null);
      $('#job-progress').hidden=true;$('#xiumi-new-task').hidden=true;
    }
    requestId=storedXiumiRequest();
    if(!requestId){
      const bytes=new Uint8Array(16);crypto.getRandomValues(bytes);
      requestId=storedXiumiRequest(Array.from(bytes,b=>b.toString(16).padStart(2,'0')).join(''));
    }
    $('#send-xiumi').textContent='正在启动发送…';
    const options={rememberLogin:$('#xiumi-remember-login').checked,leaveOpen:$('#xiumi-leave-open').checked};
    await saveDraft(false);
    const job=await api('/api/xiumi/jobs',{document:documentValue(),destination:'new_draft',requestId,options});
    xiumiSending=false;await watchXiumiJob(job.id);await loadXiumiJobs();
  }catch(error){
    xiumiSending=false;$('#job-progress').hidden=false;$('#job-message').textContent='发送请求未确认：'+error.message+'。先刷新发送记录或查看秀米，不会自动重发。';$('#xiumi-new-task').hidden=false;
    $('#send-xiumi').textContent='请求未确认，请先查看记录';
    const records=await loadXiumiJobs();
    // A new request failure must not be hidden by an unrelated old failure.
    if(requestId&&records?.some(r=>r.id===requestId))await watchXiumiJob(requestId);
  }
}

function downloadBlob(content,type,filename) {
  const url=URL.createObjectURL(new Blob([content],{type}));const a=document.createElement('a');a.href=url;a.download=filename;a.click();setTimeout(()=>URL.revokeObjectURL(url),5000);
}

async function downloadHTML() {
  await saveDraft(false);const result=await api('/api/exports',{document:documentValue()});const a=document.createElement('a');a.href=result.url;a.download='纸间_推文排版.html';a.click();toast('HTML 已生成，包含文章配图与排版');
}

async function importFile(file) {
  if(!file)return;if(file.size>35000000)throw new Error('文稿文件超过 35 MB');
  const text=(await file.text()).replace(/^\uFEFF/,'');let doc;
  if(file.name.toLowerCase().endsWith('.json')) {const data=JSON.parse(text);doc=data.document||data;}
  else {
    const lines=text.replace(/\r\n/g,'\n').split('\n');doc={...clone(bootstrap.sample),title:file.name.replace(/\.(txt|md)$/i,''),subtitle:'',column:'',author:'',blocks:[]};
    if(lines[0]?.startsWith('# '))doc.title=lines.shift().slice(2).trim();
    let paragraph=[];const flush=()=>{if(paragraph.length){doc.blocks.push({type:'paragraph',text:paragraph.join('\n')});paragraph=[];}};
    for(const line of lines){if(/^#{1,3} /.test(line)){flush();doc.blocks.push({type:'heading',text:line.replace(/^#{1,3} /,'')});}else if(line.startsWith('> ')){flush();doc.blocks.push({type:'quote',text:line.slice(2)});}else if(!line.trim()){flush();}else{paragraph.push(line);}}flush();
    if(!doc.blocks.length)doc.blocks=[{type:'paragraph',text:''}];
  }
  await api('/api/render',{document:doc,preview:true});
  clearTimeout(saveTimer);
  if(hasUnsavedChanges())await saveDraft(false);else await saveChain.catch(()=>{});
  // Only switch after persistence succeeds, and use the server's normalized model.
  const record=await api('/api/drafts',{id:newDraftId(),document:doc});
  session={id:record.id,doc:record.document};selected=0;history=[];previewResetRequested=true;
  fillFields();$('#save-state').textContent='已保存到本机';showView('editor');
  try{localStorage.setItem('paper-studio.last-draft',record.id);}catch(_){}
  refreshDrafts().catch(error=>toast(error.message,true));
  toast('已导入为一篇新草稿');
}

const terminal = new Set(['failed','cancelled','uncertain','verified','review_required','interrupted']);
async function pollJob(version=xiumiVersion) {
  if(!activeJob)return;
  const id=activeJob;
  try {
    const job=await api(`/api/jobs/${id}`);if(version!==xiumiVersion||activeJob!==id)return;
    $('#job-progress').hidden=false;$('#job-message').textContent=job.message;
    $('#job-title').textContent=job.title?`本次发送：${job.title.replace(/\n/g,' ')}`:'发送任务';
    $$('#job-steps li').forEach(li=>{li.classList.toggle('current',li.dataset.stage===(job.failedStage||job.stage));li.classList.toggle('step-failed',job.stage==='failed'&&li.dataset.stage===job.failedStage);});
    $('#xiumi-login-help').hidden=job.stage!=='waiting_login';
    $('#xiumi-login-help').textContent=job.stage==='waiting_login'?'请切换到本工具打开的 Chrome 窗口完成扫码或账号登录。日常浏览器已登录，不代表这个窗口已登录。':'';
    for(const input of [$('#xiumi-remember-login'),$('#xiumi-leave-open')])input.disabled=!terminal.has(job.stage);
    $('#cancel-job').hidden=!['queued','opening','waiting_login','writing','preflight'].includes(job.stage);
    const link=$('#draft-link'),url=safeXiumiURL(job.draftUrl);link.hidden=!url;if(url)link.href=url;else link.removeAttribute('href');
    const summary=$('#verification-summary');summary.replaceChildren();
    const checks=job.verification||job.preflight;
    if(checks){
      const label=document.createElement('p');label.textContent=job.verification?'重新打开核对':'保存前检查';summary.append(label);
      const names={title:'文章标题',text:'正文与顺序',imagesPersisted:'图片地址',imagesLoaded:'图片加载',editableBlocks:'独立组件数量',imageOrder:'保存后图片顺序'};
      for(const [name,passed] of Object.entries(checks.automatic)){const p=document.createElement('p');p.textContent=`${passed?'✓':'○'} ${names[name]||name}：${passed?'通过':'未通过'}`;summary.append(p);}
    }
    for(const warning of job.expected?.warnings||[]){const p=document.createElement('p');p.textContent=warning;summary.append(p);}
    if(job.verification){const p=document.createElement('p');p.textContent='仍需在秀米检查：版面一致性、单独换图、区块移动。';summary.append(p);}
    $('#send-xiumi').textContent=xiumiStageNames[job.stage]||'发送中…';
    if(terminal.has(job.stage)){
      clearTimeout(jobTimer);activeJob=null;
      xiumiRetryAllowed=['failed','cancelled'].includes(job.stage)&&job.saveAttempted===false;
      $('#send-xiumi').disabled=!bootstrap.bridge.available||!xiumiRetryAllowed;
      if(xiumiRetryAllowed)$('#send-xiumi').textContent='重新发送当前文章 ↗';
      $('#xiumi-new-task').hidden=false;
      await loadXiumiJobs();return;
    }
    $('#send-xiumi').disabled=true;jobTimer=setTimeout(()=>pollJob(version),1800);
  }catch(error){
    if(version!==xiumiVersion)return;
    $('#job-message').textContent=`状态读取失败：${error.message}。任务不会自动重发。`;
    jobTimer=setTimeout(()=>pollJob(version),5000);
  }
}

function bind() {
  bindSaveLifecycle();
  for(const button of $$('[data-workspace-pane]')){
    if(button.tagName!=='BUTTON')continue;
    button.onclick=()=>{
      $('.editor-grid').dataset.workspacePane=button.dataset.workspacePane;
      for(const b of $$('.workspace-switch button'))b.setAttribute('aria-pressed',String(b===button));
    };
  }
  const more=$('.editor-more');
  for(const button of $$('.editor-more-menu button'))button.addEventListener('click',()=>{more.open=false;});
  document.addEventListener('click',event=>{if(more.open&&!more.contains(event.target))more.open=false;});
  document.addEventListener('keydown',event=>{if(event.key==='Escape')more.open=false;});
  $('#continue-draft').onclick=()=>showView('editor');
  $('#draft-search').oninput=renderDrafts;
  for(const button of $$('[data-editor-panel]'))button.onclick=()=>{
    $('.editor-grid').dataset.panel=button.dataset.editorPanel;
    $$('[data-editor-panel]').forEach(b=>b.setAttribute('aria-pressed',String(b===button)));
  };
  for(const name of ['home','editor','drafts'])$(`#nav-${name}`).onclick=()=>showView(name);
  $('.brand').onclick=(e)=>{e.preventDefault();showView('home');};
  $('#entry-template').onclick=()=>newBlankDraft().catch(e=>toast(e.message,true));$('#new-draft').onclick=()=>newBlankDraft().catch(e=>toast(e.message,true));
  $('#open-sample').onclick=()=>newDraft().catch(e=>toast(e.message,true));
  $('#compare-button').onclick=()=>compareThemes().catch(e=>toast(e.message,true));
  $('#entry-reference').onclick=openReference;
  $('#reference-editor-button').onclick=openReference;
  $('#change-reference').onclick=openReference;
  $('#about-button').onclick=()=>showInfo('纸间使用说明',['已接入 doocs/md 的经典、简洁、优雅三套开源主题，段落间距可统一调整。之前的版式保留在折叠列表中。','“参考推文”可读取公开文章链接或 HTML，提取标题、正文、配色、间距和边框，试排后生成可编辑新稿。动态装饰与 SVG 尚不保证还原。','可编辑栏目名、标题、正文与图片，调整区块顺序；草稿自动保存到本机。AI 辅助排版可生成、对比与继续调整，需先配置模型。秀米自动发送仍在测试。']);
  $('#all-drafts').onclick=()=>showView('drafts');$('#home-bridge').onclick=openExport;$('#export-button').onclick=openExport;
  $('#save-button').onclick=()=>saveDraft().catch(e=>toast(e.message,true));
  for(const key of ['title','subtitle','author','column'])$(`#${key}-input`).oninput=(e)=>{remember();session.doc[key]=e.target.value;changed();};
  for(const [selector,key,output] of [['#font-input','fontSize','#font-value'],['#line-input','lineHeight','#line-value']])$(selector).oninput=(e)=>{remember();session.doc[key]=Number(e.target.value);$(output).textContent=key==='fontSize'?`${e.target.value} px`:Number(e.target.value).toFixed(2);changed();};
  $('#spacing-input').oninput=e=>{remember();session.doc.blockSpacing=Number(e.target.value);$('#spacing-value').textContent=Number(e.target.value).toFixed(2);changed();};
  $('#undo-button').onclick=()=>{if(!history.length){toast('没有可以撤销的修改');return;}session.doc=history.pop();selected=Math.min(selected,session.doc.blocks.length-1);fillFields();changed();$('#undo-button').disabled=!history.length;};
  $('#add-block').onclick=()=>{if(session.doc.blocks.length>=180){toast('最多支持 180 个区块',true);return;}remember();const type=$('#new-block-type').value;const block={type,text:type==='heading'?'新的小标题':''};if(type==='image')block.src='/assets/sample.png';session.doc.blocks.splice(selected+1,0,block);selected++;renderBlocks();changed();};
  $$('.width-buttons button').forEach(b=>b.onclick=()=>{$$('.width-buttons button').forEach(x=>x.classList.toggle('selected',x===b));$('#article-preview').style.width=`min(${b.dataset.width}px,100%)`;});
  $('#import-button').onclick=()=>$('#import-file').click();$('#import-file').onchange=()=>{importFile($('#import-file').files[0]).catch(e=>toast(e.message,true));$('#import-file').value='';};
  $$('[data-close]').forEach(b=>b.onclick=()=>b.closest('dialog').close());
  $('#download-html').onclick=()=>downloadHTML().catch(e=>toast(e.message,true));
  $('#download-json').onclick=()=>downloadBlob(JSON.stringify({format:'paper-studio/1',document:documentValue()},null,2),'application/json','纸间_可编辑文稿.json');
  $('#copy-html').onclick=async()=>{try{const result=await api('/api/render',{document:documentValue()});await navigator.clipboard.writeText(result.html);toast('已复制 HTML 源码，请粘贴到编辑器的 HTML 导入入口');}catch(error){toast('复制失败，请使用 HTML 文件导出',true);}};
  $('#send-xiumi').onclick=sendXiumi;
  $('#xiumi-refresh-jobs').onclick=()=>loadXiumiJobs(!activeJob);
  $('#xiumi-new-task').onclick=()=>{
    if(activeJob||xiumiSending)return;
    clearTimeout(jobTimer);++xiumiVersion;viewedXiumiJob=null;xiumiRetryAllowed=false;storedXiumiRequest(null);
    $('#job-progress').hidden=true;$('#draft-link').hidden=true;$('#cancel-job').hidden=true;
    $('#xiumi-login-help').hidden=true;$('#xiumi-remember-login').disabled=false;$('#xiumi-leave-open').disabled=false;
    $('#xiumi-new-task').hidden=true;$('#send-xiumi').textContent='发送当前文章到新草稿 ↗';
    $('#send-xiumi').disabled=!bootstrap.bridge.available;
    $('#bridge-message').textContent='已准备新任务。点击发送会另建一篇秀米草稿，请先确认上次结果。';
  };
  $('#cancel-job').onclick=async()=>{if(!activeJob)return;try{await api(`/api/jobs/${activeJob}/cancel`,{});toast('已请求取消，请等待当前步骤结束');}catch(error){toast(error.message,true);}};
  document.addEventListener('keydown',e=>{if((e.ctrlKey||e.metaKey)&&e.key==='s'){e.preventDefault();saveDraft().catch(error=>toast(error.message,true));}});
}

async function start() {
  try {
    bootstrap=await api('/api/bootstrap');session={id:null,doc:clone(bootstrap.sample)};
    buildThemes();bind();bindReference();bindAI();fillFields();await refreshDrafts();
    try{const last=localStorage.getItem('paper-studio.last-draft');if(last&&/^[a-f0-9]{32}$/.test(last)){const record=await api(`/api/drafts/${last}`);session={id:record.id,doc:record.document};fillFields();$('#save-state').textContent='已恢复上次草稿';}}catch(_){/* A missing prior draft does not prevent opening the workbench. */}
  }catch(error){toast(`工作台未能启动：${error.message}`,true);}
}
let referenceRecord=null, referenceBase=null, referencePreviewDoc=null, referenceBusy=false, referencePreviewVersion=0;

async function openReference(){
  referenceBase=documentValue();referencePreviewDoc=null;referenceRecord=null;
  $('#reference-result').hidden=true;$('#reference-preview-frame').hidden=true;$('#reference-preview-empty').hidden=false;
  $('#reference-status').textContent='';$('#reference-own-text').value='';$('#reference-presentation').value='reference';$('#reference-title-setting').hidden=false;$('#reference-source-canvas').hidden=true;$('#reference-own-canvas').hidden=false;$('#reference-layout-canvases').classList.remove('is-comparing');referenceTab('own');
  $('#reference-dialog').showModal();
  try{const references=await api('/api/references');const list=$('#reference-history-list');list.replaceChildren();$('#reference-history').hidden=!references.length;
    for(const record of references.slice(0,5)){const b=document.createElement('button');b.className='reference-history-item';b.textContent=record.title;b.onclick=async()=>{try{await showReference(await api('/api/references/'+record.id));}catch(e){referenceStatus(e.message,true);}};list.append(b);}
  }catch(error){referenceStatus(error.message,true);}
}

function referenceStatus(message,error=false){$('#reference-status').textContent=message;$('#reference-status').classList.toggle('reference-error',error);}

function referenceDocument(){
  const doc=clone(referenceBase);const text=$('#reference-own-text').value.trim();
  if(text){const lines=text.replace(/\r\n/g,'\n').split('\n');if(lines[0].length<=160)doc.title=lines.shift().replace(/^#\s+/,'');doc.subtitle='';doc.column='';doc.blocks=[];let p=[];
    const flush=()=>{if(p.length){doc.blocks.push({type:'paragraph',text:p.join('\n')});p=[];}};
    for(const line of lines){if(/^#{1,3}\s/.test(line)){flush();doc.blocks.push({type:'heading',text:line.replace(/^#{1,3}\s+/,'')});}else if(line.startsWith('> ')){flush();doc.blocks.push({type:'quote',text:line.slice(2)});}else if(!line.trim()){flush();}else p.push(line);}flush();
    if(!doc.blocks.length)doc.blocks=[{type:'paragraph',text:''}];
  }
  doc.referenceStyle=clone(referenceRecord.profile);doc.referenceStyle.showTitle=$('#reference-show-title').checked;doc.fontSize=Math.min(22,Math.max(12,referenceRecord.profile.paragraph.size));doc.lineHeight=referenceRecord.profile.lineHeight;
  if($('#reference-presentation').value!=='reference'){doc.theme=$('#reference-presentation').value;delete doc.referenceStyle;doc.blockSpacing=.65;}
  return doc;
}

async function previewReference(){
  if(!referenceRecord)return;
  const version=++referencePreviewVersion,doc=referenceDocument();
  const result=await api('/api/render',{document:doc,preview:true});if(version!==referencePreviewVersion)return;
  referencePreviewDoc=doc;const frame=$('#reference-preview-frame');frame.hidden=false;$('#reference-preview-empty').hidden=true;
  frame.onload=fitReferenceCanvases;
  frame.srcdoc=referenceFrameHTML(result.html);
}

async function showReference(record){
  referenceRecord=record;$('#reference-result').hidden=false;$('#reference-title').textContent=record.title;
  $('#reference-show-title').checked=record.profile.showTitle===true;
  const summary=record.layoutSummary;
  $('#reference-structure-summary').textContent=summary?`已保留 ${summary.containers} 个容器、${summary.depth} 层嵌套，以及标题、正文和图片的位置关系。`:'这是旧版样式记录，请重新读取参考以保留原文结构。';
  const sourceFrame=$('#reference-source-frame');
  sourceFrame.onload=fitReferenceCanvases;
  sourceFrame.srcdoc=referenceFrameHTML(record.sourcePreview||'<p>请重新读取以查看原文结构。</p>');
  $('#reference-source').hidden=true;if(record.url){try{const u=new URL(record.url);if(u.protocol==='https:'){$('#reference-source').href=u.href;$('#reference-source').hidden=false;}}catch(_){}}
  $('#reference-stats').textContent=`读取到 ${record.stats.paragraphs} 段正文、${record.stats.headings} 处标题或强调段、${record.stats.images} 张图片`;
  const palette=$('#reference-palette');palette.replaceChildren();for(const color of record.palette){const swatch=document.createElement('span');swatch.style.backgroundColor=color;swatch.title=color;swatch.setAttribute('aria-label',color);palette.append(swatch);}
  const spec=$('#reference-spec');spec.replaceChildren();
  for(const [name,value] of [['正文字号',record.profile.paragraph.size+' px'],['正文行距',record.profile.lineHeight],['正文开场',record.profile.layout?'沿用原文结构':'独立标题'],['内容图片位置',record.layoutSummary?.roles?.image||record.stats.images],['小标题字号',record.profile.heading.size+' px'],['图片留边',record.profile.imageInset+' px']]){const dt=document.createElement('dt');dt.textContent=name;const dd=document.createElement('dd');dd.textContent=String(value);spec.append(dt,dd);}
  const warnings=$('#reference-warnings');warnings.replaceChildren();for(const warning of record.warnings){const li=document.createElement('li');li.textContent=warning;warnings.append(li);}
  referenceStatus('已读取原文结构。可以切换“原文结构”或“并排对照”检查布局。');await previewReference();
}

function referenceFrameHTML(body){return '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><style>body{margin:0;font-family:"Microsoft YaHei",sans-serif}*{box-sizing:border-box}img{max-width:100%}p{margin:0}</style></head><body>'+body+'</body></html>';}

function fitReferenceCanvases(){
  for(const id of ['#reference-preview-frame','#reference-source-frame']){
    const frame=$(id),page=frame.parentElement;
    if(frame.hidden||frame.closest('.reference-canvas').hidden||!page.clientWidth)continue;
    const scale=Math.min(1,page.clientWidth/390),height=Math.max(500,frame.contentDocument?.body?.scrollHeight||1000);
    frame.style.height=height+'px';frame.style.transform=`scale(${scale})`;page.style.height=(height*scale)+'px';
  }
}

function referenceTab(mode){
  for(const name of ['own','source','compare'])$('#reference-tab-'+name).setAttribute('aria-pressed',String(mode===name));
  $('#reference-own-canvas').hidden=mode==='source';$('#reference-source-canvas').hidden=mode==='own';
  $('#reference-layout-canvases').classList.toggle('is-comparing',mode==='compare');
  requestAnimationFrame(fitReferenceCanvases);
}

async function readReference(payload){
  if(referenceBusy)return;referenceBusy=true;$('#read-reference').disabled=true;$('#reference-html-button').disabled=true;$('#apply-reference').disabled=true;
  $('#reference-result').hidden=true;referenceRecord=null;referencePreviewDoc=null;$('#reference-preview-frame').hidden=true;$('#reference-preview-empty').hidden=false;
  referenceStatus('正在读取文章和可用样式，通常需要几秒钟…');
  try{await showReference(await api('/api/references',payload));}
  catch(error){referenceStatus(error.message+'。可以导入 HTML；截图可放在旁边人工对照。',true);}
  finally{referenceBusy=false;$('#read-reference').disabled=false;$('#reference-html-button').disabled=false;$('#apply-reference').disabled=!referenceRecord;}
}

function bindReference(){
  const observer=new ResizeObserver(()=>fitReferenceCanvases());observer.observe($('#reference-layout-canvases'));
  $('#reference-presentation').onchange=()=>{$('#reference-title-setting').hidden=$('#reference-presentation').value!=='reference';previewReference().catch(e=>referenceStatus(e.message,true));};
  for(const mode of ['own','source','compare'])$('#reference-tab-'+mode).onclick=()=>referenceTab(mode);
  $('#reference-show-title').onchange=()=>previewReference().catch(e=>referenceStatus(e.message,true));
  $('#reference-form').onsubmit=e=>{e.preventDefault();readReference({url:$('#reference-url').value});};
  $('#reference-html-button').onclick=()=>$('#reference-html-file').click();
  $('#reference-html-file').onchange=async()=>{const file=$('#reference-html-file').files[0];if(!file)return;if(file.size>4000000){referenceStatus('HTML 文件请控制在 4 MB 以内',true);return;}await readReference({html:await file.text(),filename:file.name});$('#reference-html-file').value='';};
  $('#reference-image-button').onclick=()=>$('#reference-image-file').click();
  $('#reference-image-file').onchange=()=>{const file=$('#reference-image-file').files[0];if(!file)return;if(!['image/png','image/jpeg','image/webp'].includes(file.type)){referenceStatus('支持 PNG、JPEG 或 WebP 截图',true);return;}if(file.size>8000000){referenceStatus('截图请控制在 8 MB 以内',true);return;}const reader=new FileReader();reader.onload=()=>{$('#reference-board-image').src=reader.result;$('#reference-board').hidden=false;};reader.readAsDataURL(file);};
  $('#reference-refresh').onclick=()=>previewReference().catch(e=>referenceStatus(e.message,true));
  $('#apply-reference').onclick=async()=>{if(!referenceRecord||referenceBusy)return;$('#apply-reference').disabled=true;
    try{await previewReference();clearTimeout(saveTimer);if((session.revision||0)>(session.savedRevision||0))await saveDraft(false);else await saveChain.catch(()=>{});
      session={id:null,doc:clone(referencePreviewDoc),revision:1,savedRevision:0};history=[];selected=0;previewResetRequested=true;fillFields();await saveDraft(false);$('#reference-dialog').close();showView('editor');toast('已生成参考排版新稿，原草稿保留');
    }catch(error){referenceStatus(error.message,true);}finally{$('#apply-reference').disabled=false;}
  };
}

start();
