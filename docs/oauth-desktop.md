# OAuth desktop viewer / OAuth 直连桌面

A native MCP client can display the shared desktop after its existing OAuth
`computer` authorization, without asking the user for the owner token again.
The first OAuth grant still requires explicit owner approval. This is a
**read-only viewer**, not an owner login or a way to take control.

## Client integration

1. Complete the existing authorization-code + S256 PKCE flow for `/mcp`.
2. Read `/.well-known/oauth-protected-resource/mcp`. Its optional
   `desktop_viewer` extension provides `ticket_endpoint`, `viewer_url`, and
   `read_only: true`. Standard OAuth resource and scope fields are unchanged.
3. In the native client's trusted backend, send:

   ```http
   POST /api/viewer/ticket HTTP/1.1
   Authorization: Bearer <OAuth access_token>
   ```

   Response:

   ```json
   {
     "viewer_url": "https://YOUR_HOST/viewer#ticket=<one-use-ticket>",
     "ticket_expires_in": 59,
     "read_only": true
   }
   ```

4. Open the returned URL promptly in an isolated Electron WebView or browser.
   Do not put the access token in URLs, page JavaScript, chat, or logs. Treat the
   temporary URL as sensitive too; do not send it through a model transcript.
5. The page immediately removes the fragment, consumes the ticket once via a
   same-origin JSON `POST /api/viewer/session`, and receives an HttpOnly,
   SameSite=Strict `bridge_viewer_session` cookie. It then connects to
   `/desktop/oauth/view` and the server's read-only VNC port (5901).

Tickets last at most 60 seconds, or until their OAuth grant expires, whichever
comes first. A consumed ticket cannot be retried. Obtain a fresh ticket after
an uncertain or failed exchange. Reloading an already authenticated page uses
its cookie; it does not replay the ticket.

The cookie is bound to the original OAuth grant and cannot outlive it. Revoking
all grants with the owner's **Disconnect & revoke**, or restarting the service,
invalidates tickets and viewer cookies. Idle sockets are checked every 0.5 seconds;
packets are also checked before forwarding. New connections require the exact
configured `BRIDGE_PUBLIC_URL` Origin.

## Permissions and privacy

- The viewer cannot call owner APIs, approve OAuth clients, change personal
  context, or access the VNC control port. It never receives an owner CSRF token.
- Private takeover blocks ticket issuance, ticket redemption, status reads,
  and viewer sockets. Entering private mode closes existing streams; the page
  clears displayed pixels when disconnected or when privacy is detected.
- After private takeover ends, a still-valid viewer cookie may reconnect.
- Human takeover remains available only in the original owner viewer at `/`.
- The original owner login and MCP endpoints remain compatible.

This uses the existing single trusted owner's `computer` grant, whose consent
page explicitly mentions the additional read-only viewer. It does not introduce
multi-user isolation or a new identity provider.

## Embedding and deployment

Use `/viewer` for the compact full-height screen, not the owner dashboard at `/`.
The page retains the server's `frame-ancestors 'none'` and `X-Frame-Options: DENY`;
use a sandboxed WebView rather than an iframe. Keep Node integration, preload
scripts, native popups and unnecessary permissions disabled, and restrict
navigation to the bridge origin. Keep viewer cookies separate from normal
browsing cookies. HTTPS is required outside loopback deployment.

The server adds the integration contract; an MCP client still needs to implement
steps 2–4. Adding a generic MCP connection alone does not automatically render
its desktop in every client.

## 中文说明

客户端复用已取得的 MCP OAuth access token，在后端请求
`POST /api/viewer/ticket`，然后在右侧独立 WebView 中打开返回的
`viewer_url`。桌面页自动交换一次性票据并连接真实 noVNC 只读画面，
不再要求输入 owner token。长期 access token 只通过请求头发送，
不能放进 URL 或交给模型。

默认只读；人工接管仍在原来的 owner 页面完成。私密接管立即中断
OAuth 查看器，授权到期、撤销和服务重启也会失效。此改动仅在服务端
提供接口与桌面页；NekoCode 侧还需对接接口，才能在右侧边栏自动展示。

## Validation

```sh
pytest -q tests/test_auth.py tests/test_auth_viewer.py tests/test_oauth_viewer.py tests/test_app.py
node --test tests/viewer_state.test.cjs tests/oauth_viewer.test.cjs
ruff check .
```

Backend API and WebSocket tests use the existing fake desktop backends and a
loopback-stream fixture. They validate OAuth, cookies, authorization and live
revocation; they are not a real Chromium/noVNC rendering test. Real desktop
verification additionally requires the existing Linux Docker runtime.
