'use strict';

const aiState={base:null,source:null,result:null,record:null,history:[],busy:false,lastJob:null,submission:null,version:0};

function savedAIJob(value) {
  const key = 'paper-studio.ai-job';
  if (value !== undefined) {
    aiState.lastJob = value;
    try { localStorage.setItem(key, JSON.stringify(value)); } catch (_) { /* Use the in-memory task in this tab. */ }
    return value;
  }
  try {
    const stored = localStorage.getItem(key);
    if (/^[a-f0-9]{32}$/.test(stored || '')) return {id:stored,pending:false};
    const entry = JSON.parse(stored);
    return /^[a-f0-9]{32}$/.test(entry?.id || '') ? entry : null;
  } catch (_) {
    return aiState.lastJob;
  }
}

function aiMessage(message,error=false){$('#ai-status').textContent=message;$('#ai-status').classList.toggle('ai-error',error);}

async function openAI(){
  if(!aiState.busy){aiState.base=documentValue();}
  $('#ai-current-source').textContent='留空时使用：'+aiState.base.title.replace(/\n/g,' ')+' · '+aiState.base.blocks.filter(b=>b.type==='image').length+' 张图片';
  $('#ai-dialog').showModal();
  aiBusy(aiState.busy);
  try{const config=await api('/api/ai/settings');fillAIConfig(config);if(!config.configured&&!aiState.busy&&!savedAIJob()?.pending)aiMessage('请先配置模型。');}
  catch(e){aiMessage(e.message,true);}
  fitAIPreview();
}

function fillAIConfig(config){
  $('#ai-base-url').value=config.baseUrl;$('#ai-model').value=config.model;$('#ai-token-field').value=config.tokenField;$('#ai-json-mode').checked=config.jsonMode;
  $('#ai-key').placeholder=config.hasKey?'本次服务已配置，留空保留':'填写 API Key';
  $('#ai-config-state').textContent=config.configured?`已配置 · ${config.model}`:'尚未配置模型';
}

function ownAIDocument(){
  const input=$('#ai-own-text').value.trim();if(!input)return clone(aiState.base);
  const doc=clone(aiState.base),lines=input.replace(/\r\n/g,'\n').split('\n');
  doc.title=lines.shift().replace(/^#\s+/,'').trim();doc.subtitle='';doc.column='';doc.author='';doc.blocks=[];delete doc.referenceStyle;delete doc.aiLayout;
  let paragraph=[];const flush=()=>{if(paragraph.length){doc.blocks.push({type:'paragraph',text:paragraph.join('\n')});paragraph=[];}};
  for(const line of lines){if(/^#{1,3}\s/.test(line)){flush();doc.blocks.push({type:'heading',text:line.replace(/^#{1,3}\s+/,'')});}else if(line.startsWith('> ')){flush();doc.blocks.push({type:'quote',text:line.slice(2)});}else if(!line.trim()){flush();}else paragraph.push(line);}flush();
  if(!doc.blocks.length)doc.blocks=[{type:'paragraph',text:''}];return doc;
}

function aiBusy(value){
  aiState.busy=value;
  const job = savedAIJob();
  for(const id of ['#ai-generate','#ai-save-config','#ai-clear-key','#ai-restore'])$(id).disabled=value;
  $('#ai-restore').hidden = !job;
  $('#ai-refine').disabled = value || !!job?.pending;
  $('#ai-apply').disabled=value||!aiState.result;
  $('#ai-back').disabled=value||!!job?.pending||!aiState.history.length;
  $('#ai-generate').textContent=value?'正在处理…':job?.pending?'恢复或重试上次提交':'生成排版方案';
}

async function generateAI(refine=false){
  if(aiState.busy)return;
  const previous = savedAIJob();
  const version = ++aiState.version;
  let polling = false;
  aiBusy(true);
  try {
    if (previous?.pending) {
      aiMessage('正在查找上次提交的任务…');
      const existing = await api('/api/ai/jobs/'+previous.id).catch(error => {
        if (error.status !== 404) throw error;
        return null;
      });
      if (existing) {
        polling = true;
        await pollAI(previous.id, version, existing);
        return;
      }
    }
    const config=await api('/api/ai/settings');
    if(!config.configured){$('#ai-configuration').open=true;aiMessage('请先填写模型地址、模型名和 API Key，并点击“使用本次配置”。',true);return;}
    let payload = previous?.pending && aiState.submission?.requestId === previous.id ? aiState.submission : null;
    if (!payload) {
      if(refine&&!aiState.result)return;
      const requirement=$('#ai-requirement').value.trim(),feedback=$('#ai-feedback').value.trim();
      if(!requirement){aiMessage('先写一句排版要求，也可以选择上面的示例。',true);return;}
      if(refine&&!feedback){aiMessage('请说明这一版还需要调整哪里。',true);return;}
      payload = {
        requestId:previous?.pending ? previous.id : newDraftId(),
        document:refine ? clone(aiState.result) : ownAIDocument(),
        requirement:refine ? `原始要求：${requirement}\n这轮调整：${feedback}` : requirement,
      };
    }
    const id = payload.requestId;
    aiState.submission = payload;
    savedAIJob({id,pending:true});
    $('#ai-restore').hidden = false;
    aiMessage(`正在请求 ${config.model}，完成后会检查原文完整性。关闭窗口后任务仍会继续。`);
    let record;
    try {
      record = await api('/api/ai/jobs', payload);
    } catch (error) {
      if (error.status >= 400 && error.status < 500 && error.code !== 'request_conflict') {
        if (!previous?.pending && savedAIJob()?.id === id) savedAIJob(previous);
        if (!previous?.pending) aiState.submission = null;
        throw error;
      }
      aiMessage('提交结果尚未确认，正在按编号查找原任务…');
    }
    polling = true;
    await pollAI(id, version, record);
  } catch(error) {
    aiMessage(error.message,true);
  } finally {
    if (!polling) aiBusy(false);
  }
}

async function pollAI(id,version,record){
  try{
    record ??= await api('/api/ai/jobs/'+id);
    if(version!==aiState.version)return;
    if (savedAIJob()?.id === id) savedAIJob({id,pending:false});
    if (aiState.submission?.requestId === id) aiState.submission = null;
    if(['queued','generating'].includes(record.stage)){setTimeout(()=>pollAI(id,version),1800);return;}
    if(record.stage!=='ready'){aiBusy(false);aiMessage(record.error||'这次没有生成可用方案，没有自动重试。',true);return;}
    if(aiState.record&&aiState.record.id!==record.id){aiState.history.push({record:aiState.record,source:aiState.source,result:aiState.result});if(aiState.history.length>5)aiState.history.shift();}
    aiState.record=record;aiState.source=record.source;aiState.result=record.document;
    await showAIResult();aiBusy(false);
    aiMessage('方案已生成，文字完整性检查通过。先查看试排，再决定是否保存为新稿。');
  } catch (error) {
    if (version !== aiState.version) return;
    aiBusy(false);
    const pending = savedAIJob()?.pending;
    aiMessage(pending && error.status === 404
      ? '暂未找到这次任务。可再次恢复，或点击“恢复或重试上次提交”沿用同一编号。'
      : '暂时无法读取任务结果。可以用“恢复上次任务”继续查看，不会自动重发生成请求。', true);
  }
}

function restoreAI() {
  const job = savedAIJob();
  if (!job || aiState.busy) return;
  aiBusy(true);
  aiMessage('正在恢复上次任务…');
  return pollAI(job.id, ++aiState.version);
}

async function showAIResult(){
  const r=aiState.record;$('#ai-result').hidden=false;$('#ai-preview-empty').hidden=true;
  if(!$('#ai-requirement').value.trim())$('#ai-requirement').value=r.requirement.slice(0,1000);
  $('#ai-result-title').textContent=r.document.title;
  $('#ai-checks').textContent=`原文与 ${r.checks.images} 张图片已保留 · ${r.checks.originalBlocks} → ${r.checks.resultBlocks} 个区块`;
  const notes=$('#ai-notes');notes.replaceChildren();
  for(const note of r.notes){const li=document.createElement('li');li.textContent=note;notes.append(li);}
  $('#ai-model-used').textContent=`${r.model} · ${r.host}`;
  for(const [id,doc] of [['#ai-before-frame',aiState.source],['#ai-after-frame',aiState.result]]){
    const result=await api('/api/render',{document:doc,preview:true});const frame=$(id);frame.style.height='1px';frame.onload=fitAIPreview;frame.srcdoc=referenceFrameHTML(result.html);
  }
  $('#ai-feedback').value='';$('#ai-original-title').textContent=aiState.source.title;
  $('#ai-preview-canvases').hidden=false;fitAIPreview();
}

function fitAIPreview(){
  for(const id of ['#ai-before-frame','#ai-after-frame']){const frame=$(id),page=frame.parentElement;if(frame.closest('.ai-canvas').hidden||!page.clientWidth)continue;
    const scale=Math.min(1,page.clientWidth/390),height=Math.max(500,frame.contentDocument?.body?.scrollHeight||1000);
    frame.style.height=height+'px';frame.style.transform=`scale(${scale})`;page.style.height=(height*scale)+'px';
  }
}

function aiTab(mode){
  $('#ai-before-canvas').hidden=mode!=='compare';$('#ai-preview-canvases').classList.toggle('is-comparing',mode==='compare');
  $('#ai-tab-own').setAttribute('aria-pressed',String(mode==='own'));$('#ai-tab-compare').setAttribute('aria-pressed',String(mode==='compare'));requestAnimationFrame(fitAIPreview);
}

function bindAI(){
  $('#entry-ai').onclick=openAI;$('#ai-editor-button').onclick=openAI;
  const syncPresets=()=>{$$('#ai-presets button').forEach(b=>b.setAttribute('aria-pressed',String($('#ai-requirement').value===b.dataset.prompt)));};
  for(const button of $$('#ai-presets button'))button.onclick=()=>{$('#ai-requirement').value=button.dataset.prompt;syncPresets();};
  $('#ai-requirement').addEventListener('input',syncPresets);
  syncPresets();
  $('#ai-generate').onclick=()=>generateAI(false);$('#ai-refine').onclick=()=>generateAI(true);
  $('#ai-tab-own').onclick=()=>aiTab('own');$('#ai-tab-compare').onclick=()=>aiTab('compare');
  new ResizeObserver(fitAIPreview).observe($('#ai-preview-canvases'));
  $('#ai-save-config').onclick=async()=>{try{const config=await api('/api/ai/settings',{baseUrl:$('#ai-base-url').value,model:$('#ai-model').value,apiKey:$('#ai-key').value,tokenField:$('#ai-token-field').value,jsonMode:$('#ai-json-mode').checked});$('#ai-key').value='';fillAIConfig(config);aiMessage(config.configured?'本次模型配置已就绪。点击生成时才会发送文稿。':'配置已更新，请补充模型名或 API Key。');}catch(e){aiMessage(e.message,true);}};
  $('#ai-clear-key').onclick=async()=>{try{fillAIConfig(await api('/api/ai/settings',{clearKey:true}));$('#ai-key').value='';aiMessage('本次服务的密钥已清除。');}catch(e){aiMessage(e.message,true);}};
  $('#ai-restore').onclick=restoreAI;
  $('#ai-back').onclick=async()=>{if(!aiState.history.length||aiState.busy||savedAIJob()?.pending)return;const previous=aiState.history.pop();Object.assign(aiState,previous);await showAIResult();savedAIJob({id:aiState.record.id,pending:false});aiBusy(false);aiMessage('已回到上一版方案，没有发起新的模型请求。');};
  $('#ai-apply').onclick=async()=>{if(!aiState.result||aiState.busy)return;$('#ai-apply').disabled=true;
    try {
      const opened = await switchDraft(() => ({id:null,doc:clone(aiState.result)}),
        '已保存到本机', {saveAsNew:true});
      if (!opened) return;
      $('#ai-dialog').close();toast('已保存为 AI 排版新稿，原草稿保留');
    }catch(e){aiMessage(e.message,true);}finally{$('#ai-apply').disabled=false;}
  };
}
