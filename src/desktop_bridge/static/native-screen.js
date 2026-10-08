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
    target.replaceChildren(this.image);
    this.target = target;
    this.oldTabIndex = target.getAttribute('tabindex');
    target.tabIndex = 0;
    this.socket = new WebSocket(url);
    this.socket.binaryType = 'blob';
    this.socket.onmessage = event => {
      if (!(event.data instanceof Blob)) return;
      if (this.objectURL) URL.revokeObjectURL(this.objectURL);
      this.objectURL = URL.createObjectURL(event.data);
      this.image.src = this.objectURL;
      if (!this.connected) {
        this.connected = true;
        this.dispatchEvent(new Event('connect'));
      }
    };
    this.socket.onclose = () => this.dispatchEvent(new Event('disconnect'));
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
  send(action) {
    if (!this.viewOnly && this.socket.readyState === WebSocket.OPEN) this.socket.send(JSON.stringify(action));
  }
  disconnect() {
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
