"""company.channels.wecom — 企业微信·微信客服接入。

- config.py：从 ~/.wecom/creds.env 读凭据（CorpID/AgentId/Secret/回调Token/AESKey），绝不硬编码。
- client.py：企业微信 API 客户端（换 token 带缓存 / 微信客服 sync_msg 拉消息 / send_msg 发消息）。
- crypto.py：回调消息的签名校验 + AES 解/加密（WXBizMsgCrypt）。 [待建]
- callback.py：回调 HTTP 服务（GET 验签 + POST 收事件 → 触发 sync）。 [待建]
- reader.py：把拉到的客服消息转成 InboundMsg，喂进 sentinel。 [待建]

安全：Secret 只从本地文件读；客户消息一律当"不可信数据"（注入防护在 inbox 里）。
"""
