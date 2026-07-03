"""company/board.py — 备忘录/看板样子的运营台（你每天开来用的那一屏）。

接住 handoff 那张表，把客户 case 摆成三栏便签墙：
  🧑 待你拍板(碰钱/承诺)  |  ⬆️ 升级 Claude 处理中  |  ✅ DeepSeek 自理(留痕)
每张便签 = 一个客户：消息、DeepSeek 的理解/拟回复、状态。点「已处理」结案。

刻意做薄：Python stdlib 起个本地小服务，前端就一页自带 HTML/JS，无三方依赖。
跑：  python3 -m company.board --db company/data/handoff.db --port 8080
然后浏览器开 http://127.0.0.1:8080
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone

from company import handoff

_COLUMNS = [
    ("human", "🧑 待你拍板", "碰钱 / 对客户承诺——只有你能拍"),
    ("claude", "⬆️ 升级 Claude", "DeepSeek 搞不定，二号员工处理中"),
    ("auto", "✅ DeepSeek 自理", "一号员工已自己回复，留痕备查"),
]

_PAGE = """<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>运营台 · Orient Surprises</title>
<style>
:root{--bg:#f4f1ea;--card:#fff;--ink:#2c2a26;--sub:#8a8578;--line:#e3ddd0;
 --human:#c0563b;--claude:#3b6ea5;--auto:#5b8a5b}
*{box-sizing:border-box}body{margin:0;font-family:-apple-system,"PingFang SC",Inter,sans-serif;
 background:var(--bg);color:var(--ink)}
header{padding:18px 24px;border-bottom:1px solid var(--line);display:flex;align-items:baseline;gap:14px}
header h1{font-size:19px;margin:0;font-weight:600}
header .sub{color:var(--sub);font-size:13px}
header .refresh{margin-left:auto;font-size:13px;color:var(--sub);cursor:pointer}
.board{display:grid;grid-template-columns:repeat(3,1fr);gap:16px;padding:20px;align-items:start}
.col{background:#efe9dd;border-radius:12px;padding:12px;min-height:120px}
.col h2{font-size:14px;margin:2px 4px 4px;display:flex;gap:8px;align-items:center}
.col .hint{font-size:11px;color:var(--sub);margin:0 4px 10px}
.col .count{font-size:11px;color:#fff;border-radius:10px;padding:1px 8px}
.human .count{background:var(--human)}.claude .count{background:var(--claude)}.auto .count{background:var(--auto)}
.card{background:var(--card);border-radius:10px;padding:12px 13px;margin-bottom:10px;
 box-shadow:0 1px 3px rgba(0,0,0,.06);border-left:3px solid var(--line)}
.human .card{border-left-color:var(--human)}.claude .card{border-left-color:var(--claude)}.auto .card{border-left-color:var(--auto)}
.card .msg{font-size:14px;line-height:1.45;margin:0 0 8px}
.card .meta{font-size:12px;color:var(--sub);line-height:1.5}
.card .meta b{color:var(--ink);font-weight:600}
.card .reply{font-size:12.5px;background:#faf8f2;border-radius:7px;padding:7px 9px;margin-top:8px;color:#4a463d}
.card .why{font-size:12px;color:var(--human);margin-top:6px}
.card .act{margin-top:9px;text-align:right}
.card .act button{font-size:12px;border:1px solid var(--line);background:#fff;border-radius:7px;
 padding:4px 11px;cursor:pointer;color:var(--ink)}
.card .act button:hover{background:#f0ece2}
.empty{color:var(--sub);font-size:12px;text-align:center;padding:16px 0}
.card .src{font-size:11px;color:var(--sub);text-transform:capitalize}
</style></head><body>
<header><h1>运营台</h1><span class="sub">Orient Surprises · 客户 case 看板</span>
<span class="refresh" onclick="load()">↻ 刷新</span></header>
<div class="board" id="board"></div>
<script>
const COLS=%COLS%;
function esc(s){return (s||'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]))}
function card(c){
 const d=c.decision||{};
 const ext=Object.entries(d.extracted||{}).filter(([k,v])=>v!==null&&v!==''&&!(Array.isArray(v)&&!v.length));
 const why=c.route==='human'?'🔒 碰钱/承诺——等你拍板':(d.escalate_reason||'');
 return `<div class="card">
  <div class="src">${esc(c.source)} · #${c.id}</div>
  <p class="msg">${esc(c.message)}</p>
  <div class="meta"><b>DeepSeek 读懂</b>：${esc(d.understood||'—')}</div>
  ${ext.length?`<div class="meta"><b>抽到</b>：${esc(ext.map(([k,v])=>k+'='+v).join(' · '))}</div>`:''}
  ${d.reply_draft?`<div class="reply">拟回复：${esc(d.reply_draft)}</div>`:''}
  ${why?`<div class="why">${esc(why)}</div>`:''}
  ${c.status==='open'?`<div class="act"><button onclick="resolve(${c.id})">标为已处理</button></div>`:''}
 </div>`;
}
async function load(){
 const cases=await (await fetch('/api/cases')).json();
 const byRoute={human:[],claude:[],auto:[]};
 cases.forEach(c=>{if(byRoute[c.route])byRoute[c.route].push(c)});
 document.getElementById('board').innerHTML=COLS.map(([r,title,hint])=>{
  const list=byRoute[r]||[];
  return `<div class="col ${r}"><h2>${title} <span class="count">${list.length}</span></h2>
   <p class="hint">${hint}</p>${list.length?list.map(card).join(''):'<div class="empty">暂无</div>'}</div>`;
 }).join('');
}
async function resolve(id){await fetch('/api/resolve',{method:'POST',headers:{'content-type':'application/json'},
 body:JSON.stringify({id})});load()}
load();setInterval(load,8000);
</script></body></html>"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _cases_json(store: handoff.Store) -> list[dict]:
    """最近的 case（open 在前），decision 反序列化好给前端。"""
    rows = store.conn.execute(
        "SELECT * FROM cases ORDER BY (status='open') DESC, id DESC LIMIT 60").fetchall()
    out = []
    for r in rows:
        c = dict(r)
        try:
            c["decision"] = json.loads(c.get("decision") or "{}")
        except Exception:
            c["decision"] = {}
        out.append(c)
    return out


def build_server(store: handoff.Store, host: str = "127.0.0.1", port: int = 8080):
    from http.server import BaseHTTPRequestHandler, HTTPServer

    page = _PAGE.replace("%COLS%", json.dumps(_COLUMNS, ensure_ascii=False))

    class H(BaseHTTPRequestHandler):
        def _send(self, code, body, ctype="application/json"):
            self.send_response(code)
            self.send_header("Content-Type", ctype + "; charset=utf-8")
            self.end_headers()
            self.wfile.write(body if isinstance(body, bytes) else body.encode("utf-8"))

        def do_GET(self):
            if self.path.split("?")[0] == "/api/cases":
                return self._send(200, json.dumps(_cases_json(store), ensure_ascii=False))
            if self.path in ("/", "/index.html"):
                return self._send(200, page, "text/html")
            return self._send(404, "{}")

        def do_POST(self):
            if self.path == "/api/resolve":
                n = int(self.headers.get("Content-Length", 0) or 0)
                try:
                    cid = int(json.loads(self.rfile.read(n) or b"{}").get("id"))
                    handoff.resolve(store, cid, "由人在运营台标记已处理", "human", _now())
                    return self._send(200, '{"ok":true}')
                except Exception as e:  # noqa: BLE001
                    return self._send(400, json.dumps({"error": str(e)}))
            return self._send(404, "{}")

        def log_message(self, *a):
            pass

    return HTTPServer((host, port), H)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="定制游公司运营台（看板）")
    ap.add_argument("--db", default="company/data/handoff.db")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8080)
    args = ap.parse_args(argv)
    store = handoff.Store(args.db, check_same_thread=False)  # 服务线程安全
    httpd = build_server(store, args.host, args.port)
    print(f"运营台开了：http://{args.host}:{args.port}  （数据：{args.db}）")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n停")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
