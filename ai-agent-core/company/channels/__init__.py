"""company.channels — 真实消息渠道接入(把外部消息软件接进 company 流水线)。

每个渠道最终提供一个 sentinel.ChannelReader（() -> list[InboundMsg]），
或以事件驱动方式把新消息喂给 inbox/handoff。第一个落地的是企业微信·微信客服。
"""
