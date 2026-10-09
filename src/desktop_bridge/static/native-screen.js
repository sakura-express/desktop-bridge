// PNG stream with server-authorized JSON input for native desktop previews.
export default class NativeScreen extends EventTarget {
  constructor(target, url) {
    super();
    this.viewOnly = true;
    this.platform = 'macos';
    this.image = document.createElement('img');
    this.image.alt = 'Native desktop';
    this.image.style.cssText = 'width:100%;height:100%;object-fit:contain;user-select:none';
    this.image.draggable = false;
    this.surface = document.createElement('div');
    this.surface.style.cssText = 'position:relative;width:100%;height:100%;overflow:hidden';
    this.cursor = document.createElement('div');
    this.cursor.style.cssText = 'position:absolute;z-index:3;pointer-events:none;transition:left 60ms linear,top 60ms linear;filter:drop-shadow(0 1px 2px #0008)';
    if (globalThis.matchMedia?.('(prefers-reduced-motion: reduce)').matches) this.cursor.style.transition = 'none';
    this.cursor.setAttribute('aria-hidden', 'true');
    this.cursor.innerHTML = '<svg width="24" height="28" viewBox="0 0 24 28" fill="none"><path d="M2 2V22L7.5 17.5L12 26L16 24L11.5 16H21L2 2Z" fill="#a78bfa" stroke="white" stroke-width="2" stroke-linejoin="round"/></svg>';
    this.cursorLabel = document.createElement('span');
    this.cursorLabel.style.cssText = 'position:absolute;left:21px;top:18px;background:#6d28d9;color:white;border:1px solid #fff9;border-radius:5px;padding:2px 5px;font:600 10px/14px system-ui;white-space:nowrap';
    this.cursor.append(this.cursorLabel);
    this.pulse = document.createElement('div');
    this.pulse.style.cssText = 'position:absolute;z-index:2;pointer-events:none;width:30px;height:30px;border:3px solid #a78bfa;border-radius:50%;box-sizing:border-box;opacity:0;transform:translate(-50%,-50%)';
    this.cursor.hidden = this.pulse.hidden = true;
    this.surface.append(this.image, this.pulse, this.cursor);
    target.replaceChildren(this.surface);
    this.animations = new Set();
    this.image.onload = () => this.positionCursor();
    if (typeof ResizeObserver !== 'undefined') {
      this.resizeObserver = new ResizeObserver(() => this.positionCursor());
      this.resizeObserver.observe(this.surface);
    }
    this.target = target;
    this.oldTabIndex = target.getAttribute('tabindex');
    target.tabIndex = 0;
    this.socket = new WebSocket(url);
    this.socket.binaryType = 'blob';
    this.socket.onmessage = event => {
      if (typeof event.data === 'string') {
        try { this.receiveCursor(JSON.parse(event.data)); } catch { /* Ignore malformed telemetry. */ }
        return;
      }
      if (!(event.data instanceof Blob)) return;
      if (this.objectURL) URL.revokeObjectURL(this.objectURL);
      this.objectURL = URL.createObjectURL(event.data);
      this.image.src = this.objectURL;
      if (!this.connected) {
        this.connected = true;
        this.dispatchEvent(new Event('connect'));
      }
    };
    this.socket.onclose = () => {
      this.clearCursor();
      this.dispatchEvent(new Event('disconnect'));
    };
    this.listeners = [];
    const listen = (element, name, callback) => {
      element.addEventListener(name, callback);
      this.listeners.push(() => element.removeEventListener(name, callback));
    };
    const point = event => {
      const box = this.image.getBoundingClientRect();
      const width = this.image.naturalWidth, height = this.image.naturalHeight;
      const scale = Math.min(box.width / width, box.height / height);
      const x = Math.floor((event.clientX - box.left - (box.width - width * scale) / 2) / scale);
      const y = Math.floor((event.clientY - box.top - (box.height - height * scale) / 2) / scale);
      return width && height && x >= 0 && y >= 0 && x < width && y < height ? [x, y] : null;
    };
    const button = event => ['left', 'middle', 'right'][event.button] || 'left';
    listen(this.image, 'contextmenu', event => { if (!this.viewOnly) event.preventDefault(); });
    listen(this.image, 'pointerdown', event => {
      if (this.viewOnly) return;
      const p = point(event);
      if (!p) return;
      event.preventDefault(); target.focus();
      this.image.setPointerCapture(event.pointerId);
      this.path = [p]; this.button = button(event);
    });
    listen(this.image, 'pointermove', event => {
      const p = point(event);
      if (this.path && p && this.path.length < 99) this.path.push(p);
    });
    listen(this.image, 'pointercancel', () => { this.path = null; });
    listen(this.image, 'pointerup', event => {
      if (!this.path) return;
      const p = point(event), path = this.path;
      this.path = null;
      if (!p) return;
      const moved = path.some(([x, y]) => Math.abs(x - path[0][0]) + Math.abs(y - path[0][1]) > 3);
      this.send(moved ? {kind:'drag', path:[...path, p], button:this.button}
        : {kind:'click', x:p[0], y:p[1], button:this.button});
    });
    listen(this.image, 'wheel', event => {
      if (this.viewOnly) return;
      const p = point(event);
      if (!p) return;
      event.preventDefault();
      this.send({kind:'scroll', x:p[0], y:p[1], dx:Math.sign(event.deltaX), dy:Math.sign(event.deltaY)});
    });
    listen(target, 'paste', event => {
      if (this.viewOnly) return;
      event.preventDefault();
      this.send({kind:'type', text:event.clipboardData.getData('text').slice(0, 20000)});
    });
    listen(target, 'keydown', event => {
      if (this.viewOnly || ['Meta','Control','Alt','Shift'].includes(event.key)) return;
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'v') return;
      event.preventDefault();
      if (event.key.length === 1 && !event.metaKey && !event.ctrlKey && !event.altKey) {
        this.send({kind:'type', text:event.key}); return;
      }
      const aliases = {ArrowLeft:'left', ArrowRight:'right', ArrowUp:'up', ArrowDown:'down', ' ':'space'};
      const keys = [];
      if (event.metaKey) keys.push(this.platform === 'windows' ? 'win' : 'cmd');
      if (event.ctrlKey) keys.push('ctrl');
      if (event.altKey) keys.push('alt');
      if (event.shiftKey) keys.push('shift');
      keys.push(aliases[event.key] || event.key.toLowerCase());
      this.send({kind:'key', keys});
    });
  }
  receiveCursor(packet) {
    if (packet?.type !== 'cursor' || !['move','down','up','click','scroll'].includes(packet.kind) ||
        !Number.isInteger(packet.seq) || packet.seq <= (this.cursorState?.seq ?? -1) ||
        ![packet.x,packet.y,packet.width,packet.height].every(Number.isFinite) ||
        packet.width <= 0 || packet.height <= 0 || packet.x < 0 || packet.y < 0 ||
        packet.x >= packet.width || packet.y >= packet.height) return;
    this.cursorState = packet;
    this.pressed = ['left','right','middle'].includes(packet.pressed) ? packet.pressed : null;
    const actor = packet.actor === 'ai' ? 'AI' : 'You';
    const action = packet.kind === 'scroll' ? 'Scroll' : packet.kind === 'click' || packet.kind === 'down'
      ? packet.button === 'right' ? 'Right click' : packet.button === 'middle' ? 'Middle click' : 'Click'
      : this.pressed ? 'Drag' : '';
    this.cursorLabel.textContent = actor + (action ? ' · ' + action : '');
    this.cursor.style.filter = this.pressed ? 'drop-shadow(0 0 5px #c4b5fd)' : 'drop-shadow(0 1px 2px #0008)';
    if (!this.positionCursor()) return;
    if (['down','click','scroll'].includes(packet.kind)) {
      this.pulse.style.borderColor = packet.kind === 'scroll' ? '#fbbf24' : packet.button === 'right' ? '#38bdf8' : '#a78bfa';
      const reduced = globalThis.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
      const animation = this.pulse.animate?.([
        {opacity:1,transform:'translate(-50%,-50%) scale(.4)'},
        {opacity:0,transform:`translate(-50%,-50%) scale(${reduced ? 1 : 1.8})`},
      ], {duration:reduced ? 180 : 500, easing:'ease-out'});
      if (animation) {
        this.animations.add(animation);
        animation.onfinish = () => this.animations.delete(animation);
      }
    }
  }
  positionCursor() {
    const state = this.cursorState;
    if (!state || this.image.naturalWidth !== state.width || this.image.naturalHeight !== state.height) {
      this.cursor.hidden = this.pulse.hidden = true;
      return false;
    }
    const box = this.image.getBoundingClientRect(), surface = this.surface.getBoundingClientRect();
    const scale = Math.min(box.width / state.width, box.height / state.height);
    if (!Number.isFinite(scale) || scale <= 0) {
      this.cursor.hidden = this.pulse.hidden = true;
      return false;
    }
    const x = box.left - surface.left + (box.width - state.width * scale) / 2 + state.x * scale;
    const y = box.top - surface.top + (box.height - state.height * scale) / 2 + state.y * scale;
    this.cursor.style.left = `${x - 2}px`; this.cursor.style.top = `${y - 2}px`;
    this.pulse.style.left = `${x}px`; this.pulse.style.top = `${y}px`;
    this.cursor.hidden = this.pulse.hidden = false;
    return true;
  }
  clearCursor() {
    this.cursor.hidden = this.pulse.hidden = true;
    this.cursorState = null; this.pressed = null;
    for (const animation of this.animations) animation.cancel();
    this.animations.clear();
  }
  send(action) {
    if (!this.viewOnly && this.socket.readyState === WebSocket.OPEN) this.socket.send(JSON.stringify(action));
  }
  disconnect() {
    this.clearCursor();
    this.resizeObserver?.disconnect();
    this.image.onload = null;
    for (const remove of this.listeners) remove();
    this.path = null;
    this.socket.onmessage = null;
    this.socket.onclose = null;
    this.socket.close();
    if (this.objectURL) URL.revokeObjectURL(this.objectURL);
    if (this.oldTabIndex === null) this.target.removeAttribute('tabindex');
    else this.target.setAttribute('tabindex', this.oldTabIndex);
  }
}
