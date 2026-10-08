// Test the VNC probe client lifecycle extracted from scripts/e2e.py.
// Verifies connection handshake, single-sendKey enforcement, disconnect handling,
// error paths (timeout, security failure), and complete cleanup.

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');

// Read scripts/e2e.py and extract the probe connect JS string
const e2eSource = fs.readFileSync(path.join(__dirname, '../scripts/e2e.py'), 'utf8');

const start = e2eSource.indexOf('async (channel) =>');
const end = e2eSource.indexOf('"""', start);
assert.ok(start !== -1 && end !== -1, 'Could not locate probe JS evaluate snippet in scripts/e2e.py');
const probeJs = e2eSource.slice(start, end).trim();

function createHarness(options = {}) {
  const instances = [];
  const nodes = [];

  class MockRFB {
    constructor(node, url) {
      this.node = node;
      this.url = url;
      this.viewOnly = true;
      this.listeners = new Map();
      this.keyEvents = [];
      this.disconnectedCount = 0;
      instances.push(this);
    }

    addEventListener(event, listener, opts) {
      if (!this.listeners.has(event)) {
        this.listeners.set(event, []);
      }
      this.listeners.get(event).push({ listener, once: !!(opts && opts.once) });
    }

    removeEventListener(event, listener) {
      if (!this.listeners.has(event)) return;
      const list = this.listeners.get(event).filter(item => item.listener !== listener);
      this.listeners.set(event, list);
    }

    emit(event, detail) {
      const list = this.listeners.get(event) || [];
      const toCall = [...list];
      for (const item of toCall) {
        item.listener({ detail });
        if (item.once) {
          this.removeEventListener(event, item.listener);
        }
      }
    }

    sendKey(keysym, code) {
      this.keyEvents.push({ keysym, code });
    }

    disconnect() {
      this.disconnectedCount++;
    }
  }

  const timers = new Map();
  let timerId = 0;

  const mockWindow = {};
  const mockDocument = {
    createElement(tag) {
      const el = {
        tag,
        removed: false,
        remove() {
          this.removed = true;
        }
      };
      nodes.push(el);
      return el;
    },
    body: {
      append() {}
    }
  };

  const context = {
    window: mockWindow,
    document: mockDocument,
    location: { host: '127.0.0.1:8080' },
    setTimeout(fn, delay) {
      const id = ++timerId;
      timers.set(id, { fn, delay });
      if (options.autoTimeoutMs && delay === options.autoTimeoutMs) {
        setImmediate(() => {
          if (timers.has(id)) {
            timers.delete(id);
            fn();
          }
        });
      }
      return id;
    },
    clearTimeout(id) {
      timers.delete(id);
    },
    Error,
    Promise,
    console
  };

  vm.createContext(context);

  const executableJs = `(${probeJs.replace(
    /const \{default:RFB\} = await import\('\/novnc\/core\/rfb\.js'\);/,
    'const RFB = __MockRFB;'
  )})`;

  context.__MockRFB = MockRFB;
  const runner = vm.runInContext(executableJs, context);

  return {
    context,
    instances,
    nodes,
    timers,
    run: (channel) => runner(channel)
  };
}

test('probe connects, creates window.__bridgeVncProbe, allows single sendKey, and cleans up', async () => {
  const h = createHarness();
  const connectPromise = h.run('view');

  // Instance should be created
  assert.equal(h.instances.length, 1);
  const rfb = h.instances[0];
  assert.equal(rfb.url, 'ws://127.0.0.1:8080/desktop/view');
  assert.equal(rfb.viewOnly, false);

  // Trigger connect
  rfb.emit('connect');
  await connectPromise;

  const probe = h.context.window.__bridgeVncProbe;
  assert.ok(probe, 'window.__bridgeVncProbe must exist after connect');
  assert.equal(probe.disconnected, false);

  // Send single key
  probe.sendKey();
  assert.equal(rfb.keyEvents.length, 1);
  assert.deepEqual(rfb.keyEvents[0], { keysym: 0x7a, code: 'KeyZ' });

  // Second sendKey must throw
  assert.throws(() => probe.sendKey(), /Key already sent; re-sending not allowed/);
  assert.equal(rfb.keyEvents.length, 1);

  // Cleanup
  probe.cleanup();
  assert.equal(rfb.disconnectedCount, 1);
  assert.equal(h.nodes[0].removed, true);
  assert.equal(h.context.window.__bridgeVncProbe, undefined);
});

test('probe handshake timeout rejects and cleans up', async () => {
  const h = createHarness({ autoTimeoutMs: 5000 });
  await assert.rejects(
    async () => {
      await h.run('view');
    },
    /VNC handshake timeout/
  );
  assert.equal(h.instances.length, 1);
  assert.equal(h.instances[0].disconnectedCount, 1);
  assert.equal(h.nodes[0].removed, true);
  assert.equal(h.context.window.__bridgeVncProbe, undefined);
});

test('probe handshake securityfailure rejects with details and cleans up', async () => {
  const h = createHarness();
  const connectPromise = h.run('view');

  h.instances[0].emit('securityfailure', { status: 'Server rejected auth' });

  await assert.rejects(
    async () => {
      await connectPromise;
    },
    /VNC security failure: Server rejected auth/
  );
  assert.equal(h.instances[0].disconnectedCount, 1);
  assert.equal(h.nodes[0].removed, true);
  assert.equal(h.context.window.__bridgeVncProbe, undefined);
});

test('probe handshake disconnect rejects and cleans up', async () => {
  const h = createHarness();
  const connectPromise = h.run('view');

  h.instances[0].emit('disconnect', { clean: false });

  await assert.rejects(
    async () => {
      await connectPromise;
    },
    /VNC disconnected during handshake: unclean/
  );
  assert.equal(h.instances[0].disconnectedCount, 1);
  assert.equal(h.nodes[0].removed, true);
  assert.equal(h.context.window.__bridgeVncProbe, undefined);
});

test('probe unexpected disconnect after connect sets flag and prevents sendKey', async () => {
  const h = createHarness();
  const connectPromise = h.run('control');
  const rfb = h.instances[0];

  rfb.emit('connect');
  await connectPromise;

  const probe = h.context.window.__bridgeVncProbe;
  assert.equal(probe.disconnected, false);

  // Unexpected disconnect occurs
  rfb.emit('disconnect', { clean: false });
  assert.equal(probe.disconnected, true);

  assert.throws(
    () => probe.sendKey(),
    /Cannot sendKey: VNC probe disconnected unexpectedly/
  );
  assert.equal(rfb.keyEvents.length, 0);

  probe.cleanup();
  assert.equal(h.context.window.__bridgeVncProbe, undefined);
});
