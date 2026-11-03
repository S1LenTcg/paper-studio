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
