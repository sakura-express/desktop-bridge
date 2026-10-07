// Strip the one-use secret before any asynchronous work. Access tokens never
// belong in this page; only the native OAuth client holds them.
const fragment = new URLSearchParams(location.hash.slice(1));
let desktopTicket = fragment.get('ticket');
fragment.delete('ticket');
history.replaceState(null, '', location.pathname + location.search);
const $ = id => document.getElementById(id);
let rfb = null, generation = 0, timer = null, connecting = false, active = true;

function clearScreen(message, status) {
  generation++;
  connecting = false;
  const previous = rfb;
  rfb = null;
  previous?.disconnect();
  $('viewer-screen').replaceChildren();
  $('viewer-message').textContent = message;
  $('viewer-message').hidden = false;
  $('viewer-status').textContent = status;
}

async function api(path, options = {}) {
  const response = await fetch(path, {...options, credentials:'same-origin', cache:'no-store'});
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    // Never show arbitrary server error text that might echo a credential.
    const error = new Error('Desktop connection unavailable');
    error.status = response.status;
    error.code = data.error;
    throw error;
  }
  return data;
}

async function connectScreen() {
  if (rfb || connecting) return;
  connecting = true;
  const current = ++generation;
  try {
    const {default:RFB} = await import('/novnc/core/rfb.js');
    if (current !== generation) return;
    const connection = new RFB($('viewer-screen'), `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/desktop/oauth/view`);
    rfb = connection;
    connection.scaleViewport = true;
    connection.resizeSession = false;
    connection.viewOnly = true;
    connection.addEventListener('connect', () => {
      if (current !== generation || rfb !== connection) return;
      $('viewer-message').hidden = true;
      $('viewer-status').textContent = 'Connected · read-only';
    });
    connection.addEventListener('disconnect', () => {
      if (current !== generation || rfb !== connection) return;
      clearScreen('Desktop disconnected. Reconnecting while your authorization is valid…', 'Disconnected');
    });
    connection.addEventListener('securityfailure', () => {
      if (current !== generation || rfb !== connection) return;
      clearScreen('Desktop access rejected. Reconnect from your OAuth client.', 'Access rejected');
    });
  } catch {
    if (current === generation) clearScreen('Desktop service unavailable. Check that noVNC is installed on the server.', 'Unavailable');
  } finally {
    if (current === generation) connecting = false;
  }
}

async function refresh() {
  clearTimeout(timer);
  if (!active) return;
  const current = generation;
  try {
    await api('/api/viewer/status');
    if (current !== generation) return;
    await connectScreen();
  } catch (error) {
    if (current !== generation) return;
    if (error.code === 'private_takeover') {
      clearScreen('Private takeover: desktop observation is paused by the owner.', 'Private takeover');
    } else if (error.status === 401) {
      clearScreen('Authorization expired or revoked. Open a new desktop connection from your OAuth client.', 'Authorization required');
      return;
    } else {
      clearScreen('Connection unavailable. Retrying…', 'Disconnected');
    }
  }
  timer = setTimeout(refresh, 2000);
}

async function initialize() {
  if (desktopTicket !== null) {
    const ticket = desktopTicket;
    desktopTicket = null;
    try {
      await api('/api/viewer/session', {
        method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({ticket}),
      });
    } catch {
      clearScreen('This desktop link expired, was used, or is unavailable. Request a new connection from your OAuth client.', 'Connection unavailable');
      return;
    }
  }
  await refresh();
}

$('viewer-reconnect').addEventListener('click', () => {
  clearTimeout(timer);
  clearScreen('Connecting to your desktop…', 'Connecting…');
  void refresh();
});
$('viewer-fullscreen').addEventListener('click', () => {
  const shell = $('viewer-screen-shell');
  if (shell.requestFullscreen) void shell.requestFullscreen().catch(() => {});
});
window.addEventListener('pagehide', () => {
  active = false;
  clearTimeout(timer);
  clearScreen('Desktop disconnected.', 'Disconnected');
});
window.addEventListener('pageshow', event => {
  if (!event.persisted) return;
  active = true;
  void refresh();
});
void initialize();
