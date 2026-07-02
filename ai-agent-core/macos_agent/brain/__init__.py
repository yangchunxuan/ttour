"""brain 包：从网页代理抽取的可复用 ReAct「大脑」（macOS VM 版）。

§7A-C1：这个 __init__.py 故意保持精简——绝不 re-import 任何 Playwright
相关模块（agent.dom / agent.actions / agent.browser 在这里都不存在）。
需要 Planner / ReActAgent 的地方直接 `from brain.llm import Planner`、
`from brain.agent import ReActAgent`，不从包顶层转发。
"""
