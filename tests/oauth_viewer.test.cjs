// Run the real viewer logic with mocked browser/network and noVNC boundaries.
// This is a lifecycle regression test, not a visual or real VNC acceptance test.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const source = fs.readFileSync(path.join(__dirname, '../src/desktop_bridge/static/viewer.js'), 'utf8');
const tick = () => new Promise(resolve => setImmediate(resolve));
const ok = data => ({ok:true, status:200, json:async()=>data});

function harness({hash='', exchange=ok({read_only:true}), status=ok({state:'READY'})}={}) {
  const elements = new Map(), calls = [], connections = [], windowHandlers = {}, timers = [];
  const element = id => {
    if (!elements.has(id)) elements.set(id, {
      textContent:'', hidden:false, handlers:{}, clears:0,
      replaceChildren(){this.clears++;},
      addEventListener(type, handler){this.handlers[type]=handler;},
    });
    return elements.get(id);
  };
  class FakeRFB {
    constructor(screen,url){this.screen=screen;this.url=url;this.handlers={};connections.push(this);}
    addEventListener(type,handler){this.handlers[type]=handler;}
    disconnect(){this.disconnected=true;this.handlers.disconnect?.();}
  }
  const context = vm.createContext({
    URLSearchParams, document:{getElementById:element},
    location:{hash,pathname:'/viewer',search:'',host:'bridge.example',protocol:'https:'},
    history:{replaceState(_state,_title,url){calls.push({history:url});}},
    fetch:async(url,options)=>{calls.push({url,options});return url==='/api/viewer/session'?exchange:status;},
    setTimeout:fn=>{timers.push(fn);return timers.length;}, clearTimeout(){},
    window:{addEventListener(type,handler){windowHandlers[type]=handler;}},
    importRFB:async()=>({default:FakeRFB}),
  });
  vm.runInContext(source.replace("import('/novnc/core/rfb.js')", 'importRFB()'),context);
  return {elements,element,calls,connections,windowHandlers,timers,run:code=>vm.runInContext(code,context),
    setStatus(value){status=value;}};
}

test('erases fragment synchronously and consumes it once in a JSON POST only',async()=>{
  const h=harness({hash:'#ticket=short-lived-secret'});
  assert.deepEqual(h.calls[0],{history:'/viewer'});
  assert.equal(h.calls[1].url,'/api/viewer/session');
  assert.equal(h.calls[1].options.method,'POST');
  assert.equal(h.calls[1].options.credentials,'same-origin');
  assert.equal(h.calls[1].options.body,JSON.stringify({ticket:'short-lived-secret'}));
  await tick();
  h.element('viewer-reconnect').handlers.click();
  await tick();
  assert.equal(h.calls.filter(call=>call.url==='/api/viewer/session').length,1);
  assert.ok(h.calls.every(call=>!call.url||!call.url.includes('secret')));
});

test('a valid cookie reconnects without a ticket and RFB is always read-only',async()=>{
  const h=harness();
  await tick();
  assert.equal(h.calls.filter(call=>call.url==='/api/viewer/session').length,0);
  assert.equal(h.connections.length,1);
  const connection=h.connections[0];
  assert.equal(connection.url,'wss://bridge.example/desktop/oauth/view');
  assert.equal(connection.viewOnly,true);
  assert.equal(connection.resizeSession,false);
  assert.equal(connection.scaleViewport,true);
  connection.handlers.connect();
  assert.equal(h.element('viewer-message').hidden,true);
});

test('expired exchange shows no echoed secret and reconnect never replays the ticket',async()=>{
  const h=harness({hash:'#ticket=do-not-echo',exchange:{ok:false,status:401,json:async()=>({message:'do-not-echo'})}});
  await tick();
  assert.equal(h.connections.length,0);
  assert.ok(!h.element('viewer-message').textContent.includes('do-not-echo'));
  h.element('viewer-reconnect').handlers.click();
  await tick();
  assert.equal(h.calls.filter(call=>call.url==='/api/viewer/session').length,1);
});

test('private takeover disconnects and clears pixels before continuing status polling',async()=>{
  const h=harness();
  await tick();
  h.connections[0].handlers.connect();
  const clears=h.element('viewer-screen').clears;
  h.setStatus({ok:false,status:400,json:async()=>({error:'private_takeover'})});
  await h.run('refresh()');
  assert.equal(h.connections[0].disconnected,true);
  assert.ok(h.element('viewer-screen').clears>clears);
  assert.equal(h.element('viewer-message').hidden,false);
  assert.equal(h.element('viewer-status').textContent,'Private takeover');
  h.setStatus(ok({state:'READY'}));
  await h.run('refresh()');
  assert.equal(h.connections.length,2);
});

test('expired grant disconnects, clears pixels and stops automatic reconnect',async()=>{
  const h=harness();
  await tick();
  h.setStatus({ok:false,status:401,json:async()=>({error:'unauthorized'})});
  const timers=h.timers.length;
  await h.run('refresh()');
  assert.equal(h.connections[0].disconnected,true);
  assert.equal(h.element('viewer-status').textContent,'Authorization required');
  assert.equal(h.timers.length,timers);
});

test('pagehide prevents a delayed login response from opening a connection',async()=>{
  let resolve;
  const exchange=new Promise(done=>{resolve=done;});
  const h=harness({hash:'#ticket=delayed',exchange});
  h.windowHandlers.pagehide();
  resolve(ok({read_only:true}));
  await tick();
  assert.equal(h.connections.length,0);
  assert.equal(h.calls.filter(call=>call.url==='/api/viewer/status').length,0);
});
