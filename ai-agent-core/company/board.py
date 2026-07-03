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
    ("human", "待你拍板", "碰钱 / 对客户承诺——只有你能拍"),
    ("claude", "升级 Claude 处理中", "DeepSeek 搞不定，二号员工处理中"),
    ("auto", "DeepSeek 自理", "一号员工已自己回复，留痕备查"),
]

_PAGE = """<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>运营台 · Orient Surprises</title>
<style>
:root{--ground:#e7eaef;--panel:#f3f5f8;--card:#fff;--ink:#1b2431;--sub:#66717f;--line:#d7dce3;
 --human:#a2661a;--human-bg:#f6eddd;--claude:#3a569a;--claude-bg:#e7ecf7;--auto:#4a7a64;--auto-bg:#e4efe9;
 --mono:ui-monospace,"SF Mono",Menlo,monospace;--ui:-apple-system,"PingFang SC","Segoe UI",Roboto,sans-serif}
*{box-sizing:border-box}body{margin:0;font-family:var(--ui);background:var(--ground);color:var(--ink);line-height:1.5}
.top{display:flex;align-items:baseline;gap:14px;flex-wrap:wrap;padding:20px clamp(14px,4vw,30px) 2px}
.top h1{font-size:20px;font-weight:650;margin:0;letter-spacing:-.01em}
.top .brand{color:var(--sub);font-size:13px}
.top .refresh{margin-left:auto;font-size:12px;color:var(--sub);cursor:pointer;border:1px solid var(--line);
 border-radius:20px;padding:3px 11px;background:var(--card)}
.lead{color:var(--sub);font-size:12.5px;margin:4px clamp(14px,4vw,30px) 16px;max-width:72ch}
.lead b{color:var(--ink);font-weight:600}
.board{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;align-items:start;padding:0 clamp(14px,4vw,30px) 40px}
@media(max-width:820px){.board{grid-template-columns:1fr}}
.lane{background:var(--panel);border:1px solid var(--line);border-radius:13px;padding:12px 12px 6px}
.lane .hd{display:flex;align-items:center;gap:8px;padding:2px 3px}
.lane .dot{width:9px;height:9px;border-radius:50%;flex:none}
.lane .lname{font-size:12px;font-weight:650;letter-spacing:.02em}
.lane .cnt{margin-left:auto;font-size:11px;font-weight:650;color:#fff;border-radius:20px;min-width:20px;
 text-align:center;padding:1px 7px;font-variant-numeric:tabular-nums}
.lane .purpose{font-size:11px;color:var(--sub);margin:3px 4px 11px}
.human .dot,.human .cnt{background:var(--human)}.human{border-top:2px solid var(--human)}
.claude .dot,.claude .cnt{background:var(--claude)}.claude{border-top:2px solid var(--claude)}
.auto .dot,.auto .cnt{background:var(--auto)}.auto{border-top:2px solid var(--auto)}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:11px 12px 12px;
 margin-bottom:10px;box-shadow:0 1px 2px rgba(20,30,45,.05)}
.card .chan{display:flex;align-items:center;gap:6px;margin-bottom:7px}
.card .chan .src{font-family:var(--mono);font-size:10.5px;color:var(--sub)}
.card .chan .id{font-family:var(--mono);font-size:10.5px;color:var(--sub);margin-left:auto}
.card .status{font-size:10px;font-weight:650;letter-spacing:.03em;padding:2px 7px;border-radius:5px}
.human .status{color:var(--human);background:var(--human-bg)}
.claude .status{color:var(--claude);background:var(--claude-bg)}
.auto .status{color:var(--auto);background:var(--auto-bg)}
.card .msg{font-size:13.5px;margin:0 0 8px}
.card .row{font-size:12px;color:var(--sub);margin-top:5px}
.card .row b{color:var(--ink);font-weight:600}
.card .ext{display:inline-flex;flex-wrap:wrap;gap:5px;margin-top:6px}
.card .ext span{font-size:10.5px;font-family:var(--mono);color:var(--ink);background:var(--ground);border-radius:5px;padding:1px 6px}
.card .reply{font-size:12px;color:#4c5563;background:var(--panel);border:1px solid var(--line);border-radius:7px;padding:7px 9px;margin-top:8px}
.card .reply em{color:var(--sub);font-style:normal;font-size:10.5px;letter-spacing:.04em;display:block;margin-bottom:2px}
.card .why{font-size:11.5px;margin-top:8px;padding-left:9px;border-left:2px solid}
.human .why{color:var(--human);border-color:var(--human)}.claude .why{color:var(--claude);border-color:var(--claude)}
.card .act{margin-top:10px;text-align:right}
.card .act button{font:inherit;font-size:11.5px;font-weight:550;cursor:pointer;border-radius:7px;
 padding:5px 11px;border:1px solid var(--line);background:var(--card);color:var(--ink)}
.card .act button:hover{background:var(--panel)}
.empty{color:var(--sub);font-size:12px;text-align:center;padding:14px 0}
</style></head><body>
<div class="top"><h1>运营台</h1><span class="brand">Orient Surprises · 客户 case 看板</span>
<span class="refresh" onclick="load()">↻ 刷新</span></div>
<p class="lead">客户私信进来，<b>DeepSeek(一号)</b> 先判断能不能自理：碰钱/承诺的等<b>你</b>拍板，
搞不定的升给<b>Claude(二号)</b>，常规的它自己回、留痕备查。</p>
<div class="board" id="board"></div>
<script>
const COLS=%COLS%;
const STAT={human:"待拍板",claude:"处理中",auto:"已回复"};
function esc(s){return (s||'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]))}
function card(c){
 const d=c.decision||{};
 const ext=Object.entries(d.extracted||{}).filter(([k,v])=>v!==null&&v!==''&&!(Array.isArray(v)&&!v.length));
 const why=c.route==='human'?'🔒 碰钱/承诺——等你拍板':(d.escalate_reason||'');
 const replyTag=c.route==='auto'?'已回客户':'拟回复（未发）';
 return `<div class="card">
  <div class="chan"><span class="src">${esc(c.source)}</span><span class="status">${STAT[c.route]||''}</span><span class="id">#${c.id}</span></div>
  <p class="msg">${esc(c.message)}</p>
  <div class="row"><b>DeepSeek 读懂</b>：${esc(d.understood||'—')}</div>
  ${ext.length?`<div class="ext">${ext.map(([k,v])=>`<span>${esc(k)}=${esc(String(v))}</span>`).join('')}</div>`:''}
  ${why?`<div class="why">${esc(why)}</div>`:''}
  ${d.reply_draft?`<div class="reply"><em>${replyTag}</em>${esc(d.reply_draft)}</div>`:''}
  ${c.status==='open'?`<div class="act"><button onclick="resolve(${c.id})">标为已处理</button></div>`:''}
 </div>`;
}
async function load(){
 const cases=await (await fetch('/api/cases')).json();
 const byRoute={human:[],claude:[],auto:[]};
 cases.forEach(c=>{if(byRoute[c.route])byRoute[c.route].push(c)});
 document.getElementById('board').innerHTML=COLS.map(([r,title,hint])=>{
  const list=byRoute[r]||[];
  return `<section class="lane ${r}"><div class="hd"><span class="dot"></span>
   <span class="lname">${title}</span><span class="cnt">${list.length}</span></div>
   <p class="purpose">${hint}</p>${list.length?list.map(card).join(''):'<div class="empty">暂无</div>'}</section>`;
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
