"""Render the actual NativeScreen cursor in a disposable browser fixture."""
import os
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

root = Path(__file__).resolve().parents[1]
output = root / "probe-output"
output.mkdir(exist_ok=True)
html = b"""<!doctype html><meta charset="utf-8"><style>
body{margin:0;background:#151a24;color:#e4e7f0;font:14px system-ui;padding:28px}
header{display:flex;align-items:center;justify-content:space-between;margin-bottom:16px}
strong{font-size:18px}span{background:#6d28d9;padding:7px 12px;border-radius:20px}
#screen{width:960px;height:640px;background:#070b12;border:1px solid #454b5b;border-radius:10px;overflow:hidden}
p{color:#aab2c3}main{width:960px;margin:auto}
</style><main><header><strong>Desktop Bridge</strong><span>AI operating</span></header>
<div id="screen"></div><p>Live cursor &middot; click feedback &middot; read-only viewer</p></main>
<script type="module">
class Socket{static OPEN=1;readyState=1;close(){}send(){throw Error('Read-only preview sent input')}}
window.WebSocket=Socket;
const {default:NativeScreen}=await import('/native-screen.js');
window.screenPreview=new NativeScreen(document.getElementById('screen'),'ws://fixture');
const frame=await fetch('/frame.png').then(r=>r.blob());
screenPreview.socket.onmessage({data:frame});
await new Promise(resolve=>{if(screenPreview.image.complete)resolve();else screenPreview.image.addEventListener('load',resolve,{once:true})});
window.previewReady=true;
</script>"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        routes = {
            "/": ("text/html", html),
            "/native-screen.js": ("text/javascript", (root / "src/desktop_bridge/static/native-screen.js").read_bytes()),
            "/frame.png": ("image/png", (output / "downloads-opened.png").read_bytes()),
        }
        if self.path not in routes:
            self.send_error(404)
            return
        content_type, body = routes[self.path]
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
try:
    playwright = os.environ.get("BRIDGE_PREVIEW_PLAYWRIGHT", str(Path.home() /
        ".cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright"))
    script = """
    const {chromium}=require(process.env.BRIDGE_PREVIEW_PLAYWRIGHT);
    (async()=>{
        const browser=await chromium.launch({channel:'msedge',headless:true});
        const page=await browser.newPage({viewport:{width:1100,height:800},deviceScaleFactor:1});
        const errors=[];
        page.on('pageerror', error=>errors.push(String(error)));
        await page.goto(process.env.BRIDGE_PREVIEW_URL);
        await page.waitForFunction(()=>window.previewReady);
        await page.evaluate(() => {
            screenPreview.receiveCursor({type:'cursor',seq:1,kind:'down',x:211,y:356,
                width:1024,height:768,button:'left',pressed:'left',actor:'ai'});
            for(const animation of screenPreview.animations){animation.pause();animation.currentTime=150;}
        });
        await page.screenshot({path:process.env.BRIDGE_PREVIEW_OUTPUT});
        const geometry=await page.evaluate(() => {
            const s=screenPreview, image=s.image.getBoundingClientRect(), cursor=s.cursor.getBoundingClientRect();
            const scale=Math.min(image.width/1024,image.height/768);
            const x=image.left+(image.width-1024*scale)/2+211*scale;
            const y=image.top+(image.height-768*scale)/2+356*scale;
            return {deltaX:Math.abs(cursor.left+2-x),deltaY:Math.abs(cursor.top+2-y),label:s.cursorLabel.textContent};
        });
        if(geometry.deltaX>=1||geometry.deltaY>=1||errors.length)throw Error(JSON.stringify({geometry,errors}));
        console.log(JSON.stringify({preview:process.env.BRIDGE_PREVIEW_OUTPUT,geometry}));
        await browser.close();
    })().catch(error=>{console.error(error);process.exit(1)});
    """
    subprocess.run(["node", "-"], input=script.encode(), check=True, timeout=45,
        env={**os.environ, "BRIDGE_PREVIEW_PLAYWRIGHT": playwright,
             "BRIDGE_PREVIEW_URL": f"http://127.0.0.1:{server.server_port}/",
             "BRIDGE_PREVIEW_OUTPUT": str(output / "virtual-cursor-preview.png")})
finally:
    server.shutdown()
    server.server_close()
