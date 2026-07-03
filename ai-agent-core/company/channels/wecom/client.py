"""company/channels/wecom/client.py — 企业微信 API 客户端(纯 stdlib)。

覆盖公司要用的最小面:
  * token():        换取 access_token,进程内缓存 + 到期自动刷新(线程安全)。
  * kf_account_list(): 列微信客服账号(读操作,拿来做"连通性自检"最省事)。
  * sync_msg():     微信客服·拉取消息(cursor 增量;token 来自回调事件,可空)。
  * send_text():    微信客服·给客户回一条文本(48h 会话窗口内)。

设计原则(对齐 harness engineering):
  - 只认底层真实返回:errcode!=0 一律抛 WecomError,绝不"假装成功"。
  - token 失效(40014/42001)自动刷新重试一次,别把过期 token 的报错当业务失败。
  - Secret 只在内存,永不打印;日志里也不带 access_token。
"""

from __future__ import annotations

import json
import threading
import time
import urllib.parse
import urllib.request

QYAPI = "https://qyapi.weixin.qq.com/cgi-bin"

# access_token 过期/无效的错误码 → 刷新后重试
_TOKEN_ERRCODES = {40014, 42001}


class WecomError(Exception):
    """企业微信 API 返回 errcode!=0。带上错误码,方便上层区分(如 60020=IP未加白)。"""

    def __init__(self, errcode: int, errmsg: str):
        self.errcode = errcode
        self.errmsg = errmsg
        super().__init__(f"WeCom API errcode={errcode} errmsg={errmsg}")


class WecomKfClient:
    def __init__(self, corpid: str, secret: str, *, timeout: int = 15):
        self._corpid = corpid
        self._secret = secret
        self._timeout = timeout
        self._token: str | None = None
        self._token_exp: float = 0.0
        self._lock = threading.Lock()

    # ---- 底层 HTTP(不打印敏感信息) ---- #
    def _get(self, path: str, params: dict) -> dict:
        url = f"{QYAPI}/{path}?" + urllib.parse.urlencode(params)
        with urllib.request.urlopen(url, timeout=self._timeout) as r:
            return json.loads(r.read().decode("utf-8"))

    def _post(self, path: str, params: dict, body: dict) -> dict:
        url = f"{QYAPI}/{path}?" + urllib.parse.urlencode(params)
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            url, data=data, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=self._timeout) as r:
            return json.loads(r.read().decode("utf-8"))

    # ---- token ---- #
    def token(self, *, force: bool = False) -> str:
        """拿 access_token。缓存到到期前 60s;force=True 强制刷新。线程安全。"""
        with self._lock:
            now = time.time()
            if not force and self._token and now < self._token_exp - 60:
                return self._token
            data = self._get("gettoken",
                             {"corpid": self._corpid, "corpsecret": self._secret})
            if data.get("errcode", 0) != 0:
                raise WecomError(data.get("errcode", -1), data.get("errmsg", ""))
            self._token = data["access_token"]
            self._token_exp = now + int(data.get("expires_in", 7200))
            return self._token

    # ---- 通用调用:自动带 token + 失效刷新重试一次 ---- #
    def _call(self, method: str, path: str, *, body: dict | None = None,
              params: dict | None = None, _retried: bool = False) -> dict:
        p = dict(params or {})
        p["access_token"] = self.token(force=_retried)
        data = (self._post(path, p, body or {}) if method == "POST"
                else self._get(path, p))
        ec = data.get("errcode", 0)
        if ec in _TOKEN_ERRCODES and not _retried:
            return self._call(method, path, body=body, params=params, _retried=True)
        if ec != 0:
            raise WecomError(ec, data.get("errmsg", ""))
        return data

    # ---- 业务面 ---- #
    def kf_account_list(self, offset: int = 0, limit: int = 100) -> list[dict]:
        """列微信客服账号。也是最省事的"连通性 + 可信IP"自检(能成 = 全绿)。"""
        data = self._call("POST", "kf/account/list",
                          body={"offset": offset, "limit": limit})
        return data.get("account_list", [])

    def sync_msg(self, *, cursor: str = "", token: str = "",
                 open_kfid: str = "", limit: int = 1000) -> dict:
        """微信客服·增量拉消息。返回 {msg_list, next_cursor, has_more}。
        token 来自回调事件(实时、免限频);首次 cursor 可空。"""
        body: dict = {"limit": limit}
        if cursor:
            body["cursor"] = cursor
        if token:
            body["token"] = token
        if open_kfid:
            body["open_kfid"] = open_kfid
        return self._call("POST", "kf/sync_msg", body=body)

    def send_text(self, *, touser: str, open_kfid: str, content: str,
                  msgid: str | None = None) -> dict:
        """微信客服·回客户一条文本。touser=客户external_userid,open_kfid=客服账号。"""
        body: dict = {"touser": touser, "open_kfid": open_kfid,
                      "msgtype": "text", "text": {"content": content}}
        if msgid:
            body["msgid"] = msgid
        return self._call("POST", "kf/send_msg", body=body)


def from_config(cfg, *, timeout: int = 15) -> "WecomKfClient":
    """便捷构造:从 WecomConfig 建客户端。"""
    return WecomKfClient(cfg.corpid, cfg.secret, timeout=timeout)
