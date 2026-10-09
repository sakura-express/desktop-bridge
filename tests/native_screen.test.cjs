const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const source = fs.readFileSync(path.join(__dirname, '../src/desktop_bridge/static/native-screen.js'), 'utf8');

function harness() {
  function element() {
    return Object.assign(new EventTarget(), {style:{}, setAttribute(){}, append(){},
      getBoundingClientRect:()=>({left:0,top:0,width:800,height:600}),
      animate(){return {cancel(){this.cancelled=true;}};}});
  }
  const image = element();
  Object.assign(image, {style:{}, naturalWidth:2000, naturalHeight:1000,
    getBoundingClientRect:()=>({left:0,top:0,width:800,height:600}),
    setPointerCapture(){}});
  const target = new EventTarget();
  Object.assign(target, {getAttribute:()=>null, removeAttribute(){}, replaceChildren(){}, focus(){}});
  class Socket {
    static OPEN = 1;
    readyState = 1;
    messages = [];
    send(value){this.messages.push(JSON.parse(value));}
    close(){this.closed=true;}
  }
  const revoked = [];
  const context = vm.createContext({EventTarget, Event, Blob, WebSocket:Socket,
    document:{createElement:tag=>tag==='img'?image:element()},
    URL:{createObjectURL:()=> 'blob:test', revokeObjectURL:value=>revoked.push(value)}});
  vm.runInContext(source.replace('export default class NativeScreen', 'globalThis.NativeScreen = class NativeScreen'), context);
  const screen = new context.NativeScreen(target, 'ws://test/desktop/view');
  function emit(element, kind, properties={}) {
    const event = new Event(kind, {cancelable:true});
    Object.assign(event, properties);
    element.dispatchEvent(event);
  }
  return {screen,image,target,emit,revoked};
}

test('native viewer sends no input until owner control is enabled', () => {
  const h = harness();
  h.emit(h.image, 'pointerdown', {clientX:200,clientY:150,button:0,pointerId:1});
  h.emit(h.image, 'pointerup', {clientX:200,clientY:150,button:0});
  h.screen.send({kind:'type',text:'blocked'});
  assert.equal(h.screen.socket.messages.length, 0);
});

test('native owner coordinates account for image pixels and letterboxing', () => {
  const h = harness(); h.screen.viewOnly = false;
  h.emit(h.image, 'pointerdown', {clientX:200,clientY:150,button:2,pointerId:1});
  h.emit(h.image, 'pointerup', {clientX:200,clientY:150,button:2});
  assert.deepEqual(h.screen.socket.messages[0], {kind:'click',x:500,y:125,button:'right'});
  h.emit(h.image, 'pointerdown', {clientX:200,clientY:50,button:0,pointerId:2});
  h.emit(h.image, 'pointerup', {clientX:200,clientY:50,button:0});
  assert.equal(h.screen.socket.messages.length, 1);
});

test('native owner sends command keys and unicode clipboard text', () => {
  const h = harness(); h.screen.viewOnly = false;
  h.emit(h.target, 'keydown', {key:'a',metaKey:true});
  h.emit(h.target, 'paste', {clipboardData:{getData:()=> '中文输入'}});
  assert.deepEqual(h.screen.socket.messages, [{kind:'key',keys:['cmd','a']}, {kind:'type',text:'中文输入'}]);
});

test('native reconnect cleanup revokes pixels and removes input listeners', () => {
  const h = harness(); h.screen.viewOnly = false;
  let connected = 0;
  h.screen.addEventListener('connect', () => connected++);
  h.screen.socket.onmessage({data:new Blob(['png'])});
  assert.equal(connected, 1);
  h.screen.disconnect();
  h.emit(h.target, 'keydown', {key:'a'});
  assert.equal(h.screen.socket.messages.length, 0);
  assert.equal(h.screen.socket.closed, true);
  assert.deepEqual(h.revoked, ['blob:test']);
});

test('Windows viewer maps the Meta modifier to the native Windows key', () => {
  const h = harness(); h.screen.viewOnly = false; h.screen.platform = 'windows';
  h.emit(h.target, 'keydown', {key:'ArrowLeft',metaKey:true});
  assert.deepEqual(h.screen.socket.messages, [{kind:'key',keys:['win','left']}]);
});

test('cursor events align with letterboxed image and distinguish AI drag and right click', () => {
  const h = harness();
  const packet = {type:'cursor',seq:1,kind:'move',x:500,y:125,width:2000,height:1000,actor:'ai'};
  h.screen.socket.onmessage({data:JSON.stringify(packet)});
  assert.equal(h.screen.cursor.hidden, false);
  assert.equal(h.screen.cursor.style.left, '198px');
  assert.equal(h.screen.cursor.style.top, '148px');
  assert.equal(h.screen.cursorLabel.textContent, 'AI');
  h.screen.receiveCursor({...packet,seq:2,kind:'down',button:'right',pressed:'right'});
  assert.equal(h.screen.cursorLabel.textContent, 'AI · Right click');
  assert.equal(h.screen.animations.size, 1);
  h.screen.receiveCursor({...packet,seq:3,kind:'move',pressed:'right'});
  assert.equal(h.screen.cursorLabel.textContent, 'AI · Drag');
  h.screen.receiveCursor({...packet,seq:4,kind:'up',pressed:null});
  assert.equal(h.screen.cursorLabel.textContent, 'AI');
  assert.equal(h.screen.socket.messages.length, 0);
});

test('cursor ignores malformed/stale packets and waits for matching image dimensions', () => {
  const h = harness();
  h.screen.socket.onmessage({data:'not-json'});
  const packet = {type:'cursor',seq:5,kind:'click',x:10,y:20,width:1000,height:500,actor:'ai'};
  h.screen.receiveCursor(packet);
  assert.equal(h.screen.cursor.hidden, true);
  h.image.naturalWidth = 1000; h.image.naturalHeight = 500; h.image.onload();
  assert.equal(h.screen.cursor.hidden, false);
  h.screen.receiveCursor({...packet,seq:4,x:500});
  assert.equal(h.screen.cursorState.x, 10);
  h.screen.receiveCursor({...packet,seq:6,x:1000});
  assert.equal(h.screen.cursorState.seq, 5);
  h.screen.socket.onclose();
  assert.equal(h.screen.cursor.hidden, true);
  assert.equal(h.screen.animations.size, 0);
});
