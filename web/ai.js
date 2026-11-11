'use strict';

const aiState={base:null,source:null,result:null,record:null,history:[],busy:false,job:null,version:0};
function savedAIJob(id){try{if(id!==undefined)localStorage.setItem('paper-studio.ai-job',id);return localStorage.getItem('paper-studio.ai-job');}catch(_){return aiState.job;}}

function aiMessage(message,error=false){$('#ai-status').textContent=message;$('#ai-status').classList.toggle('ai-error',error);}

async function openAI(){
  if(!aiState.busy){aiState.base=documentValue();}
  $('#ai-current-source').textContent='留空时使用：'+aiState.base.title.replace(/\n/g,' ')+' · '+aiState.base.blocks.filter(b=>b.type==='image').length+' 张图片';
  $('#ai-dialog').showModal();
  $('#ai-restore').hidden=!savedAIJob();
  try{const config=await api('/api/ai/settings');fillAIConfig(config);if(!config.configured)aiMessage('请先配置模型。');}
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
  for(const id of ['#ai-generate','#ai-refine','#ai-save-config','#ai-clear-key','#ai-restore'])$(id).disabled=value;
  $('#ai-apply').disabled=value||!aiState.result;$('#ai-back').disabled=value||!aiState.history.length;
  $('#ai-generate').textContent=value?'正在生成…':'生成排版方案';
}

async function generateAI(refine=false){
  if(aiState.busy)return;
  try{
    const config=await api('/api/ai/settings');
    if(!config.configured){$('#ai-configuration').open=true;aiMessage('请先填写模型地址、模型名和 API Key，并点击“使用本次配置”。',true);return;}
    if(refine&&!aiState.result)return;
    const requirement=$('#ai-requirement').value.trim(),feedback=$('#ai-feedback').value.trim();
    if(!requirement){aiMessage('先写一句排版要求，也可以选择上面的示例。',true);return;}
    if(refine&&!feedback){aiMessage('请说明这一版还需要调整哪里。',true);return;}
    const source=refine?clone(aiState.result):ownAIDocument();
    const instruction=refine?`原始要求：${requirement}\n这轮调整：${feedback}`:requirement;
    aiBusy(true);aiMessage(`正在请求 ${config.model}，完成后会检查原文完整性。关闭窗口后任务仍会继续。`);
    const record=await api('/api/ai/jobs',{document:source,requirement:instruction});
    aiState.job=record.id;savedAIJob(record.id);$('#ai-restore').hidden=false;
    pollAI(record.id,++aiState.version);
  }catch(e){aiBusy(false);aiMessage(e.message,true);}
}

async function pollAI(id,version){
  try{
    const record=await api('/api/ai/jobs/'+id);if(version!==aiState.version)return;
    if(['queued','generating'].includes(record.stage)){setTimeout(()=>pollAI(id,version),1800);return;}
    if(record.stage!=='ready'){aiBusy(false);aiMessage(record.error||'这次没有生成可用方案，没有自动重试。',true);return;}
    if(aiState.record&&aiState.record.id!==record.id){aiState.history.push({record:aiState.record,source:aiState.source,result:aiState.result});if(aiState.history.length>5)aiState.history.shift();}
    aiState.record=record;aiState.source=record.source;aiState.result=record.document;
    await showAIResult();aiBusy(false);
    aiMessage('方案已生成，文字完整性检查通过。先查看试排，再决定是否保存为新稿。');
  }catch(e){if(version!==aiState.version)return;aiBusy(false);aiMessage('暂时无法读取任务结果。可以用“恢复上次任务”继续查看，不会自动重发生成请求。',true);}
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
  $('#ai-restore').onclick=async()=>{const id=savedAIJob();if(!/^[a-f0-9]{32}$/.test(id||''))return;aiBusy(true);aiState.job=id;aiMessage('正在恢复上次任务…');pollAI(id,++aiState.version);};
  $('#ai-back').onclick=async()=>{if(!aiState.history.length||aiState.busy)return;const previous=aiState.history.pop();Object.assign(aiState,previous);await showAIResult();aiBusy(false);savedAIJob(aiState.record.id);aiMessage('已回到上一版方案，没有发起新的模型请求。');};
  $('#ai-apply').onclick=async()=>{if(!aiState.result||aiState.busy)return;$('#ai-apply').disabled=true;
    try{clearTimeout(saveTimer);if((session.revision||0)>(session.savedRevision||0))await saveDraft(false);else await saveChain.catch(()=>{});
      session={id:null,doc:clone(aiState.result),revision:1,savedRevision:0};history=[];selected=0;previewResetRequested=true;fillFields();await saveDraft(false);$('#ai-dialog').close();showView('editor');toast('已保存为 AI 排版新稿，原草稿保留');
    }catch(e){aiMessage(e.message,true);}finally{$('#ai-apply').disabled=false;}
  };
}
