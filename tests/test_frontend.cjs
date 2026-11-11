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
    saveDraft=async()=>record('save:'+session.id);
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

function editorHarness(handler=async()=>({})) {
  const nodes=new Map(),calls=[],events=new Map(),stored=new Map();
  const node=()=>({value:'',textContent:'',disabled:false,hidden:false,style:{},
    classList:{toggle(){}},setAttribute(){},removeAttribute(){},replaceChildren(){},append(){},focus(){}});
  const document={querySelector(selector){if(!nodes.has(selector))nodes.set(selector,node());return nodes.get(selector);},
    querySelectorAll(){return [];},createElement:node,visibilityState:'visible',
    addEventListener(name,fn){events.set(name,fn);}};
  const context=vm.createContext({document,crypto:require('node:crypto').webcrypto,
    window:{addEventListener(name,fn){events.set(name,fn);}},
    localStorage:{setItem:(key,value)=>stored.set(key,value)},setTimeout(){},clearTimeout(){}});
  const source=fs.readFileSync(path.join(__dirname,'../web/app.js'),'utf8').replace(/\nstart\(\);\s*$/,'');
  vm.runInContext(source+`
    bootstrap={themes:{editorial:{name:'测试模板'}}};
    session={id:null,doc:{title:'原稿',blocks:[{type:'paragraph',text:'原文'}]},revision:1,savedRevision:0};
    renderBlocks=()=>{};showView=()=>{};refreshDrafts=async()=>{};toast=()=>{};
    globalThis.controls={state:()=>session,edit:text=>{session.doc.blocks[0].text=text;session.revision++;},
      clone,bindSaveLifecycle:()=>bindSaveLifecycle()};
  `,context);
  context.api=async(route,data)=>{calls.push({route,data});return handler(route,data);};
  return {context,nodes,calls,events,stored,document};
}

test('minimal JSON import uses normalized fields returned by the server',async()=>{
  const normalized={title:'导入稿',theme:'editorial',fontSize:15,lineHeight:1.95,blockSpacing:.65,
    subtitle:'',author:'',column:'',blocks:[{type:'paragraph',text:''}]};
  const h=editorHarness(async(route,data)=>({id:data?.id||'b'.repeat(32),document:normalized}));
  await h.context.importFile({name:'minimal.json',size:100,text:async()=>JSON.stringify({title:'导入稿',blocks:[{type:'paragraph'}]})});
  assert.equal(h.context.controls.state().doc.theme,'editorial');
  assert.equal(h.context.controls.state().doc.blocks[0].text,'');
  assert.equal(h.nodes.get('#save-state').textContent,'已保存到本机');
});

test('failed JSON import leaves the previous article selected',async()=>{
  const h=editorHarness(async(route,data)=>{
    if(route==='/api/drafts'&&data.document.title==='导入稿')throw new Error('磁盘已满');
    return {id:data?.id||'a'.repeat(32),document:data?.document};
  });
  const original=h.context.controls.state();
  await assert.rejects(h.context.importFile({name:'draft.json',size:100,text:async()=>JSON.stringify({title:'导入稿',blocks:[{type:'paragraph'}]})}),/磁盘已满/);
  assert.equal(h.context.controls.state(),original);
  assert.equal(original.doc.title,'原稿');
});

test('a lost save response retains one draft id and exposes an unsaved state',async()=>{
  let attempts=0;
  const h=editorHarness(async(route,data)=>{if(++attempts===1)throw new Error('连接中断');return {id:data.id};});
  await assert.rejects(h.context.saveDraft(false),/连接中断/);
  assert.match(h.nodes.get('#save-state').textContent,/未保存/);
  assert.equal(h.context.controls.state().savedRevision,0);
  await h.context.saveDraft(false);
  assert.match(h.calls[0].data.id,/^[a-f0-9]{32}$/);
  assert.equal(h.calls[0].data.id,h.calls[1].data.id);
  assert.equal(h.context.controls.state().savedRevision,1);
});

test('an old save response cannot mark later edits as saved',async()=>{
  let finish;
  const h=editorHarness((route,data)=>new Promise(resolve=>{finish=()=>resolve({id:data.id});}));
  const saving=h.context.saveDraft(false);
  await new Promise(resolve=>setImmediate(resolve));
  h.context.controls.edit('较新的正文');finish();await saving;
  assert.equal(h.context.controls.state().savedRevision,1);
  assert.equal(h.context.controls.state().revision,2);
  assert.equal(h.nodes.get('#save-state').textContent,'正在编辑…');
});

test('leaving warns while dirty and stops warning after a successful save',async()=>{
  const h=editorHarness(async(route,data)=>({id:data.id}));
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

test('undo snapshots isolate nested edits while preserving image data',()=>{
  const h=editorHarness(),source={blocks:[{text:'正文',src:'data:image/png;base64,'+'A'.repeat(10000)}],referenceStyle:{layout:{children:[{role:'paragraph'}]}}};
  const snapshot=h.context.controls.clone(source);
  source.blocks[0].text='后来修改';source.referenceStyle.layout.children[0].role='heading';
  assert.equal(snapshot.blocks[0].text,'正文');assert.equal(snapshot.referenceStyle.layout.children[0].role,'paragraph');
  assert.equal(snapshot.blocks[0].src,source.blocks[0].src);
});
