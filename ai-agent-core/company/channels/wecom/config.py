"""company/channels/wecom/config.py — 读企业微信凭据(纯 stdlib,不打印敏感值)。

凭据文件默认 ~/.wecom/creds.env,每行 KEY=VALUE:
  WECOM_CORPID   企业ID(ww 开头)
  WECOM_AGENTID  自建应用 AgentId
  WECOM_SECRET   自建应用 Secret(已授权可调用微信客服接口)
  WECOM_CALLBACK_TOKEN   [收消息用] 配回调时自己设的 Token
  WECOM_CALLBACK_AESKEY  [收消息用] 配回调时的 EncodingAESKey(43位)

只从本地文件/环境变量读,永远不写死在代码里,也不主动打印。
"""

from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_PATH = "~/.wecom/creds.env"


def load_env(path: str | None = None) -> dict[str, str]:
    """把 creds.env 读成 dict。文件不存在 → 返回空 dict(交给上层报友好错误)。"""
    p = os.path.expanduser(path or os.environ.get("WECOM_CREDS", DEFAULT_PATH))
    out: dict[str, str] = {}
    if not os.path.exists(p):
        return out
    with open(p, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


@dataclass(frozen=True)
class WecomConfig:
    corpid: str
    agentid: str
    secret: str
    callback_token: str = ""
    callback_aeskey: str = ""

    @property
    def has_callback(self) -> bool:
        return bool(self.callback_token and self.callback_aeskey)


def load_config(path: str | None = None) -> WecomConfig:
    """加载并校验必填项。缺 CorpID/Secret 直接报清楚的错(不回显值)。"""
    env = load_env(path)
    corpid = env.get("WECOM_CORPID", "")
    secret = env.get("WECOM_SECRET", "")
    missing = [k for k, v in (("WECOM_CORPID", corpid), ("WECOM_SECRET", secret)) if not v]
    if missing:
        raise ValueError(
            f"凭据缺失: {', '.join(missing)} —— 请检查 {os.path.expanduser(path or DEFAULT_PATH)}")
    return WecomConfig(
        corpid=corpid,
        agentid=env.get("WECOM_AGENTID", ""),
        secret=secret,
        callback_token=env.get("WECOM_CALLBACK_TOKEN", ""),
        callback_aeskey=env.get("WECOM_CALLBACK_AESKEY", ""),
    )
