"""agent 包：ReAct 自主网页代理。

对外暴露的核心构件（供 cli.py / agent.py 组合使用）：
    - Planner           规划器（DeepSeek 决策 + 抽取）
    - SYSTEM_PROMPT      系统提示词（动作空间 + ReAct 规则）
    - ACTION_NAMES       合法动作名集合（供校验层复用）
    - build_action_reference  动作空间说明文本（供 prompt/文档复用）
"""

from .prompts import get_system_prompt, ACTION_NAMES, build_action_reference
from .llm import Planner

__all__ = [
    "Planner",
    "get_system_prompt",
    "ACTION_NAMES",
    "build_action_reference",
]
