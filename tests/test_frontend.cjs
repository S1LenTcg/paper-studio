const test=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const path=require('node:path');

function blankHarness(dirty=false){
  const nodes=new Map(),events=[];
  const document={querySelector(selector){if(!nodes.has(selector))nodes.set(selector,{dataset:{panel:'settings',workspacePane:'preview'},focus(){events.push('focus');},textContent:''});return nodes.get(selector);},querySelectorAll(){return [];}};
  const context=vm.createContext({document,clearTimeout(){},setTimeout(){}});
  const source=fs.readFileSync(path.join(__dirname,'../web/app.js'),'utf8').replace(/\nstart\(\);\s*$/,'');
  vm.runInContext(source+`
    bootstrap={sample:{title:'示例',subtitle:'导语',column:'栏目',author:'作者',theme:'doocs-classic',fontSize:14,blocks:[{type:'image',src:'sample.png',text:'图片'}],referenceStyle:{title:'参考'},aiLayout:{version:1}}};
    session={id:'existing',doc:{title:'原文章'},revision:${dirty?2:0},savedRevision:0};
    saveDraft=async()=>{record('save:'+session.id);session.savedRevision=session.revision;};
    fillFields=()=>record('fields');showView=name=>record('view:'+name);
    globalThis.state=()=>({session,selected,history});
  `,context);
  context.record=value=>events.push(value);
  return {context,nodes,events};
}

test('new blank article contains no example text, photos or borrowed layout',async()=>{
  const h=blankHarness();await h.context.newBlankDraft();
  const state=h.context.state();assert.equal(state.session.id,null);assert.equal(state.session.doc.title,'');
  assert.deepEqual(JSON.parse(JSON.stringify(state.session.doc.blocks)),[{type:'paragraph',text:''}]);
  for(const key of ['subtitle','column','author'])assert.equal(state.session.doc[key],'');
  assert.equal(state.session.doc.referenceStyle,undefined);assert.equal(state.session.doc.aiLayout,undefined);
  assert.equal(state.session.doc.theme,'doocs-classic');assert.equal(h.nodes.get('.editor-grid').dataset.panel,'blocks');
  assert.equal(h.nodes.get('.editor-grid').dataset.workspacePane,'edit');assert.equal(h.events.at(-1),'focus');
});

test('new article saves unsaved original first and does not save a blank draft automatically',async()=>{
  const h=blankHarness(true);await h.context.newBlankDraft();
  assert.equal(h.events[0],'save:existing');assert.equal(h.events.filter(e=>e.startsWith('save:')).length,1);
  assert.equal(h.context.state().session.id,null);
});

function editorHarness(handler=async()=>({}),stored=new Map()) {
  const nodes=new Map(),calls=[],events=new Map();
  const node=()=>({value:'',textContent:'',disabled:false,hidden:false,style:{},
    classList:{toggle(){}},setAttribute(){},removeAttribute(){},replaceChildren(){},append(){},focus(){}});
  const document={querySelector(selector){if(!nodes.has(selector))nodes.set(selector,node());return nodes.get(selector);},
    querySelectorAll(){return [];},createElement:node,visibilityState:'visible',
    addEventListener(name,fn){events.set(name,fn);}};
  const context=vm.createContext({document,crypto:require('node:crypto').webcrypto,
    window:{addEventListener(name,fn){events.set(name,fn);}},
    localStorage:{getItem:key=>stored.get(key)??null,setItem:(key,value)=>stored.set(key,value)},setTimeout(){},clearTimeout(){}});
  const source=fs.readFileSync(path.join(__dirname,'../web/app.js'),'utf8').replace(/\nstart\(\);\s*$/,'');
  vm.runInContext(source+`
    bootstrap={themes:{editorial:{name:'测试模板'}}};
    session={id:null,doc:{title:'原稿',blocks:[{type:'paragraph',text:'原文'}]},revision:1,savedRevision:0};
    renderBlocks=()=>{};showView=()=>{};refreshDrafts=async()=>{};toast=()=>{};
    globalThis.controls={state:()=>session,open:record=>{session=draftSession(record);},
      edit:text=>{session.doc.blocks[0].text=text;session.revision=(session.revision||0)+1;},
      clone,bindSaveLifecycle:()=>bindSaveLifecycle()};
  `,context);
  context.api=async(route,data)=>{calls.push({route,data});return handler(route,data);};
  return {context,nodes,calls,events,stored,document};
}

function savedDraft(data) {
  return {id:data.id,version:data.writeId,document:data.document};
}

function draftRecord(id, title) {
  return {id,version:'a'.repeat(32),document:{title,theme:'editorial',blocks:[{type:'paragraph',text:''}]}};
}

test('a late draft response cannot replace the more recent selection',async()=>{
  let finishFirst;
  const h=editorHarness(route=>{
    if(route.endsWith('/first'))return new Promise(resolve=>{finishFirst=resolve;});
    return draftRecord('second','第二篇');
  });
  h.context.controls.open(draftRecord('original','原稿'));
  const first=h.context.loadDraft('first');
  assert.equal(await h.context.loadDraft('second'),true);
  finishFirst(draftRecord('first','第一篇'));
  assert.equal(await first,false);
  assert.equal(h.context.controls.state().id,'second');
});

test('switching waits for edits made while the original is being saved',async()=>{
  let finishSave;
  const h=editorHarness((route,data)=>{
    if(!data)return draftRecord('target','新选择');
    if(data.document.blocks[0].text==='切换前的修改'){
      return new Promise(resolve=>{finishSave=()=>resolve(savedDraft(data));});
    }
    return savedDraft(data);
  });
  h.context.controls.open(draftRecord('original','原稿'));
  h.context.controls.edit('切换前的修改');
  const opening=h.context.loadDraft('target');
  await new Promise(resolve=>setImmediate(resolve));
  h.context.controls.edit('保存中继续输入');
  finishSave();
  assert.equal(await opening,true);
  const writes=h.calls.filter(call=>call.data).map(call=>call.data);
  assert.equal(writes.length,2);
  assert.equal(writes[1].id,'original');
  assert.equal(writes[1].expectedVersion,writes[0].writeId);
  assert.equal(writes[1].document.blocks[0].text,'保存中继续输入');
  assert.equal(h.context.controls.state().id,'target');
});

test('a superseded import cannot create a draft after the file finishes reading',async()=>{
  let finishReading;
  const h=editorHarness(route=>route==='/api/render'?{}:draftRecord('target','新选择'));
  h.context.controls.open(draftRecord('original','原稿'));
  const importing=h.context.importFile({name:'slow.json',size:100,
    text:()=>new Promise(resolve=>{finishReading=resolve;})});
  assert.equal(await h.context.loadDraft('target'),true);
  finishReading(JSON.stringify(draftRecord('imported','导入稿')));
  await importing;
  assert.equal(h.context.controls.state().id,'target');
  assert.equal(h.calls.filter(call=>call.route==='/api/drafts').length,0);
});

test('reselecting the current draft cannot replace edits saved during the selection',async()=>{
  const original=draftRecord('original','原稿');
  const h=editorHarness((route,data)=>data?savedDraft(data):original);
  h.context.controls.open(original);
  const opening=h.context.loadDraft('original');
  h.context.controls.edit('刚输入的修改');
  assert.equal(await opening,true);
  assert.equal(h.context.controls.state().doc.blocks[0].text,'刚输入的修改');
  assert.equal(h.context.controls.state().version,h.calls[1].data.writeId);
});

test('minimal JSON import uses normalized fields returned by the server',async()=>{
  const normalized={title:'导入稿',theme:'editorial',fontSize:15,lineHeight:1.95,blockSpacing:.65,
    subtitle:'',author:'',column:'',blocks:[{type:'paragraph',text:''}]};
  const h=editorHarness(async(route,data)=>({id:data?.id||'b'.repeat(32),version:'a'.repeat(32),document:normalized}));
  await h.context.importFile({name:'minimal.json',size:100,text:async()=>JSON.stringify({title:'导入稿',blocks:[{type:'paragraph'}]})});
  assert.equal(h.context.controls.state().doc.theme,'editorial');
  assert.equal(h.context.controls.state().doc.blocks[0].text,'');
  assert.equal(h.nodes.get('#save-state').textContent,'已保存到本机');
});

test('failed JSON import leaves the previous article selected',async()=>{
  const h=editorHarness(async(route,data)=>{
    if(route==='/api/drafts'&&data.document.title==='导入稿')throw new Error('磁盘已满');
    return route==='/api/drafts' ? savedDraft(data) : {};
  });
  const original=h.context.controls.state();
  await assert.rejects(h.context.importFile({name:'draft.json',size:100,text:async()=>JSON.stringify({title:'导入稿',blocks:[{type:'paragraph'}]})}),/磁盘已满/);
  assert.equal(h.context.controls.state(),original);
  assert.equal(original.doc.title,'原稿');
});

test('a lost save response retains one draft id and exposes an unsaved state',async()=>{
  let attempts=0;
  const h=editorHarness(async(route,data)=>{if(++attempts===1)throw new Error('连接中断');return savedDraft(data);});
  await assert.rejects(h.context.saveDraft(false),/连接中断/);
  assert.match(h.nodes.get('#save-state').textContent,/未保存/);
  assert.equal(h.context.controls.state().savedRevision,0);
  await h.context.saveDraft(false);
  assert.match(h.calls[0].data.id,/^[a-f0-9]{32}$/);
  assert.deepEqual(h.calls[0].data,h.calls[1].data);
  assert.equal(h.context.controls.state().savedRevision,1);
});

test('an old save response cannot mark later edits as saved',async()=>{
  let finish;
  const h=editorHarness((route,data)=>new Promise(resolve=>{finish=()=>resolve(savedDraft(data));}));
  const saving=h.context.saveDraft(false);
  await new Promise(resolve=>setImmediate(resolve));
  h.context.controls.edit('较新的正文');finish();await saving;
  assert.equal(h.context.controls.state().savedRevision,1);
  assert.equal(h.context.controls.state().revision,2);
  assert.equal(h.nodes.get('#save-state').textContent,'正在编辑…');
});

test('leaving warns while dirty and stops warning after a successful save',async()=>{
  const h=editorHarness(async(route,data)=>savedDraft(data));
  h.context.controls.bindSaveLifecycle();
  let warned=0;
  const event={preventDefault(){warned++;}};
  h.events.get('beforeunload')(event);assert.equal(warned,1);
  await h.context.saveDraft(false);
  h.events.get('beforeunload')(event);assert.equal(warned,1);
  h.context.controls.edit('继续编辑');h.document.visibilityState='hidden';
  h.events.get('visibilitychange')();
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(h.calls.length,2);
  assert.equal(h.context.controls.state().savedRevision,2);
});

test('queued edits use the version returned by the previous save',async()=>{
  const h=editorHarness(async(route,data)=>savedDraft(data));
  const first=h.context.saveDraft(false);
  h.context.controls.edit('后续修改');
  const second=h.context.saveDraft(false);
  await Promise.all([first,second]);
  assert.equal(h.calls.length,2);
  assert.equal(h.calls[1].data.expectedVersion,h.calls[0].data.writeId);
  assert.equal(h.calls[1].data.document.blocks[0].text,'后续修改');
});

test('save conflict keeps edits, blocks further writes to the original, and saves a copy',async()=>{
  const id='a'.repeat(32),version='b'.repeat(32);
  let copyAttempts=0;
  const h=editorHarness(async(route,data)=>{
    if(data.id===id)throw Object.assign(new Error('草稿版本已变化'),{status:409,code:'draft_conflict'});
    if(++copyAttempts===1)throw new Error('连接中断');
    return savedDraft(data);
  });
  h.context.controls.open({id,version,document:{title:'共用草稿',blocks:[{type:'paragraph',text:'原文'}]}});
  h.context.controls.bindSaveLifecycle();
  h.context.controls.edit('本标签页修改');
  await assert.rejects(h.context.saveDraft(false),/草稿版本已变化/);
  assert.equal(h.calls[0].data.expectedVersion,version);
  assert.equal(h.context.controls.state().doc.blocks[0].text,'本标签页修改');
  assert.equal(h.nodes.get('#draft-conflict').hidden,false);
  assert.equal(h.nodes.get('#save-button').disabled,true);
  h.document.visibilityState='hidden';
  h.events.get('visibilitychange')();
  await assert.rejects(h.context.saveDraft(false),/草稿版本已变化/);
  assert.equal(h.calls.length,1);

  await assert.rejects(h.context.saveDraft(false,true),/连接中断/);
  h.context.controls.edit('另存期间继续修改');
  await h.context.saveDraft(false,true);
  assert.deepEqual(h.calls[2].data,h.calls[1].data);
  const copy=h.calls[3].data;
  assert.notEqual(copy.id,id);
  assert.equal(copy.id,h.calls[1].data.id);
  assert.equal(h.calls[1].data.expectedVersion,null);
  assert.equal(copy.expectedVersion,h.calls[1].data.writeId);
  assert.equal(copy.document.blocks[0].text,'另存期间继续修改');
  assert.equal(h.context.controls.state().id,copy.id);
  assert.equal(h.nodes.get('#draft-conflict').hidden,true);
  assert.equal(h.nodes.get('#save-button').disabled,false);
});

test('undo snapshots isolate nested edits while preserving image data',()=>{
  const h=editorHarness(),source={blocks:[{text:'正文',src:'data:image/png;base64,'+'A'.repeat(10000)}],referenceStyle:{layout:{children:[{role:'paragraph'}]}}};
  const snapshot=h.context.controls.clone(source);
  source.blocks[0].text='后来修改';source.referenceStyle.layout.children[0].role='heading';
  assert.equal(snapshot.blocks[0].text,'正文');assert.equal(snapshot.referenceStyle.layout.children[0].role,'paragraph');
  assert.equal(snapshot.blocks[0].src,source.blocks[0].src);
});

function aiHarness(handler,stored) {
  const h=editorHarness(handler,stored);
  const source=fs.readFileSync(path.join(__dirname,'../web/ai.js'),'utf8');
  vm.runInContext(source+`
    aiState.base=documentValue();
    showAIResult=async()=>{};
    $('#ai-requirement').value='校园新闻';
  `,h.context);
  return h;
}

test('a lost AI submission response restores the accepted task without another POST',async()=>{
  let requestId;
  const h=aiHarness(async(route,payload)=>{
    if(route==='/api/ai/settings')return {configured:true,model:'test'};
    if(route==='/api/ai/jobs'){
      requestId=payload.requestId;
      assert.deepEqual(JSON.parse(h.stored.get('paper-studio.ai-job')),{id:requestId,pending:true});
      throw new Error('响应丢失');
    }
    assert.equal(route,'/api/ai/jobs/'+requestId);
    return {id:requestId,stage:'ready'};
  });
  await Promise.all([h.context.generateAI(),h.context.generateAI()]);
  assert.match(requestId,/^[a-f0-9]{32}$/);
  assert.equal(h.calls.filter(call=>call.route==='/api/ai/jobs').length,1);
  assert.deepEqual(JSON.parse(h.stored.get('paper-studio.ai-job')),{id:requestId,pending:false});
  assert.match(h.nodes.get('#ai-status').textContent,/方案已生成/);
});

test('an unresolved AI submission survives reload and an explicit retry reuses its id',async()=>{
  const stored=new Map();
  const notFound=()=>Object.assign(new Error('未找到记录'),{status:404});
  const first=aiHarness(async route=>{
    if(route==='/api/ai/settings')return {configured:true,model:'test'};
    if(route==='/api/ai/jobs')throw new Error('断线');
    throw notFound();
  },stored);
  await first.context.generateAI();
  const requestId=first.calls.find(call=>call.route==='/api/ai/jobs').data.requestId;
  assert.deepEqual(JSON.parse(stored.get('paper-studio.ai-job')),{id:requestId,pending:true});
  const reopened=aiHarness(async(route,payload)=>{
    if(route==='/api/ai/settings')return {configured:true,model:'test'};
    if(route==='/api/ai/jobs')return {id:payload.requestId,stage:'ready'};
    throw notFound();
  },stored);
  await reopened.context.restoreAI();
  assert.deepEqual(reopened.calls.map(call=>call.route),['/api/ai/jobs/'+requestId]);
  await reopened.context.generateAI();
  const retried=reopened.calls.filter(call=>call.route==='/api/ai/jobs');
  assert.equal(retried.length,1);
  assert.equal(retried[0].data.requestId,requestId);
  assert.deepEqual(JSON.parse(stored.get('paper-studio.ai-job')),{id:requestId,pending:false});
});
