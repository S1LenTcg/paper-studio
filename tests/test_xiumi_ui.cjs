// State-machine tests only: no browser, network, or Xiumi session is opened.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function harness(handler) {
  const nodes = new Map(), storage = new Map(), calls = [];
  function node() {
    return {hidden:false, disabled:false, checked:false, textContent:'', children:[],
      classList:{toggle(){}}, setAttribute(){},removeAttribute(){},
      replaceChildren(){this.children=[];},append(...items){this.children.push(...items);},
      showModal(){this.open=true;}};
  }
  const document = {querySelector(selector){if(!nodes.has(selector))nodes.set(selector,node());return nodes.get(selector);},
    querySelectorAll(){return [];},createElement:node};
  const context = vm.createContext({document,URL,crypto:require('node:crypto').webcrypto,
    localStorage:{getItem:k=>storage.get(k)||null,setItem:(k,v)=>storage.set(k,v),removeItem:k=>storage.delete(k)},
    setTimeout:()=>1,clearTimeout(){}});
  const source=fs.readFileSync(path.join(__dirname,'../web/app.js'),'utf8').replace(/\nstart\(\);\s*$/,'');
  vm.runInContext(source+`
    bootstrap={bridge:{available:true,message:'test'}};
    session={doc:{title:'测试稿',blocks:[]}};
    saveDraft=async()=>{};
    globalThis.controls={state:()=>({activeJob,viewedXiumiJob,xiumiSending}),
      selected:id=>{activeJob=id;viewedXiumiJob=id;return ++xiumiVersion;}};
  `,context);
  context.api=async(route,data)=>{calls.push({route,data});return handler(route,data);};
  return {context,calls,nodes,storage};
}

test('restoring history only reads status and does not resend',async()=>{
  const id='a'.repeat(32),record={id,title:'上次发送',stage:'uncertain',message:'请查看秀米',createdAt:'2026-11-01'};
  const h=harness(async()=>record);
  h.context.api=async(route,data)=>{h.calls.push({route,data});return route==='/api/xiumi/jobs'?[record]:record;};
  await h.context.loadXiumiJobs(true);
  assert.equal(h.calls.filter(c=>c.data).length,0);
  assert.equal(h.context.controls.state().viewedXiumiJob,id);
  assert.equal(h.nodes.get('#send-xiumi').disabled,true);
  assert.equal(h.nodes.get('#xiumi-new-task').hidden,false);
});

test('double click starts one request with a stored request id',async()=>{
  let record;
  const h=harness(async(route,data)=>{
    if(data){record={id:data.requestId,stage:'verified',message:'test'};return record;}
    return route==='/api/xiumi/jobs'?[record]:record;
  });
  await Promise.all([h.context.sendXiumi(),h.context.sendXiumi()]);
  const posts=h.calls.filter(c=>c.data);
  assert.equal(posts.length,1);
  assert.match(posts[0].data.requestId,/^[a-f0-9]{32}$/);
  assert.equal(h.storage.get('paper-studio.xiumi-request'),posts[0].data.requestId);
  assert.equal(posts[0].data.destination,'new_draft');
});

test('lost POST response recovers the same record without a second POST',async()=>{
  let record;
  const h=harness(async(route,data)=>{
    if(data){record={id:data.requestId,stage:'uncertain',message:'保存结果未知'};throw Error('simulated response loss');}
    return route==='/api/xiumi/jobs'?[record]:record;
  });
  await h.context.sendXiumi();
  await h.context.sendXiumi();
  assert.equal(h.calls.filter(c=>c.data).length,1);
  assert.equal(h.context.controls.state().viewedXiumiJob,record.id);
  assert.equal(h.nodes.get('#job-message').textContent,'保存结果未知');
});

test('lost request with no server record stays unresolved and never auto retries',async()=>{
  const h=harness(async(route,data)=>{if(data)throw Error('offline');return [];});
  await h.context.sendXiumi();
  assert.equal(h.calls.filter(c=>c.data).length,1);
  assert.equal(h.nodes.get('#send-xiumi').disabled,true);
  assert.equal(h.nodes.get('#xiumi-new-task').hidden,false);
  assert.match(h.nodes.get('#job-message').textContent,/不会自动重发/);
});

test('old polling response cannot overwrite the newly selected task',async()=>{
  let release;
  const h=harness(()=>new Promise(resolve=>{release=resolve;}));
  const version=h.context.controls.selected('a'.repeat(32));
  const polling=h.context.pollJob(version);
  h.context.controls.selected('b'.repeat(32));
  release({stage:'verified',message:'old result'});
  await polling;
  assert.equal(h.context.controls.state().activeJob,'b'.repeat(32));
  assert.equal(h.nodes.has('#job-message'),false);
});

test('only exact Xiumi numeric draft links become clickable',()=>{
  const h=harness(()=>{});
  assert.ok(h.context.safeXiumiURL('https://xiumi.us/studio/v5#/paper/for/123/cube/0'));
  for(const url of ['https://evil.example/studio/v5#/paper/for/123','https://xiumi.us@evil.example/studio/v5#/paper/for/123',
    'https://xiumi.us/studio/v5#/paper/for/new','https://xiumi.us/studio/v5#/paper/for/123bad','javascript:alert(1)']){
    assert.equal(h.context.safeXiumiURL(url),null);
  }
});

test('remembering login is sent only from the explicit checkbox',async()=>{
  let record;
  const h=harness(async(route,data)=>{
    if(data){record={id:data.requestId,stage:'verified',message:'test'};return record;}
    return route==='/api/xiumi/jobs'?[record]:record;
  });
  h.context.document.querySelector('#xiumi-remember-login').checked=true;
  h.context.document.querySelector('#xiumi-leave-open').checked=true;
  await h.context.sendXiumi();
  const options=h.calls.find(c=>c.data).data.options;
  assert.equal(options.rememberLogin,true);
  assert.equal(options.leaveOpen,true);
});

test('waiting for login shows the new-window hint and locks browser options',async()=>{
  const id='d'.repeat(32),record={id,stage:'waiting_login',message:'请完成登录'};
  const h=harness(async()=>record);
  await h.context.watchXiumiJob(id);
  assert.equal(h.nodes.get('#xiumi-login-help').hidden,false);
  assert.match(h.nodes.get('#xiumi-login-help').textContent,/日常浏览器已登录/);
  assert.equal(h.nodes.get('#xiumi-remember-login').disabled,true);
  assert.equal(h.nodes.get('#xiumi-leave-open').disabled,true);
  assert.equal(h.calls.filter(c=>c.data).length,0);
});

test('confirmed unsaved failure exposes a working retry with a fresh request id',async()=>{
  const oldId='f'.repeat(32);
  let record={id:oldId,stage:'failed',saveAttempted:false,message:'上次登录超时'};
  const h=harness(async(route,data)=>{
    if(data){record={id:data.requestId,stage:'verified',saveAttempted:true,message:'test'};return record;}
    return route==='/api/xiumi/jobs'?[record]:record;
  });
  h.storage.set('paper-studio.xiumi-request',oldId);
  await h.context.loadXiumiJobs(true);
  assert.equal(h.nodes.get('#send-xiumi').disabled,false);
  assert.match(h.nodes.get('#send-xiumi').textContent,/重新发送/);
  await Promise.all([h.context.sendXiumi(),h.context.sendXiumi()]);
  const posts=h.calls.filter(c=>c.data);
  assert.equal(posts.length,1);
  assert.notEqual(posts[0].data.requestId,oldId);
});

test('uncertain or possibly saved failures remain blocked until reviewed',async()=>{
  for(const state of [{stage:'uncertain',saveAttempted:false},{stage:'failed',saveAttempted:true},
    {stage:'failed'},{stage:'verified',saveAttempted:true}]){
    const record={id:'e'.repeat(32),...state,message:'需查看结果'};
    const h=harness(async route=>route==='/api/xiumi/jobs'?[record]:record);
    await h.context.loadXiumiJobs(true);
    assert.equal(h.nodes.get('#send-xiumi').disabled,true);
    await h.context.sendXiumi();
    assert.equal(h.calls.filter(c=>c.data).length,0);
  }
});

test('a new request error is not replaced by an unrelated old TimeoutError',async()=>{
  const old={id:'b'.repeat(32),stage:'failed',saveAttempted:false,message:'旧的 TimeoutError'};
  const h=harness(async(route,data)=>{if(data)throw Error('本次 API 错误');return [old];});
  await h.context.sendXiumi();
  assert.equal(h.calls.filter(c=>c.data).length,1);
  assert.match(h.nodes.get('#job-message').textContent,/本次 API 错误/);
  assert.doesNotMatch(h.nodes.get('#job-message').textContent,/旧的 TimeoutError/);
});
