"""brain/llm.py — DeepSeek 规划器（Planner，macOS VM 版）。

职责：
  - decide(): 把 GOAL + 观察快照 + 最近历史组织成消息，调用 deepseek
    (response_format=json_object, temperature=0)，再把返回内容
    解析 / 校验 / 修复成 {"thought": str, "action": {"name", "args"}}。
  - extract(): 复用 utils.extract_information_json 做结构化抽取。

macOS VM 版改动（§7A）：
  - C2：删掉了 `import agent.prompts` 的 fallback——brain 包里没有这个模块名，
    prompts_module 改为必填（macOS 路径传 macos.prompts）。
  - C3：extract() 把 base_url / api_key 穿透给 utils.extract_information_json，
    否则 extract 会绕过 broker 直打 api.deepseek.com（VM 内无真 key 必失败）。

设计要点：DeepSeek 在 json_object 模式下通常返回合法 JSON，但真实环境仍会
出现：markdown 围栏、缺 thought/action、动作名拼错、index 写成字符串、args
用错类型等。decide() 对这些情况全部做「尽力修复」，保证永远返回一个
可被 executor 安全执行的动作（最坏情况降级为一个 wait，让循环得以继续/收敛）。
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

from dotenv import load_dotenv
from openai import AsyncOpenAI

from .utils import extract_information_json

load_dotenv()

# 最近历史里，DOM 文本可能很长；对喂给模型的历史做截断，控制 token。
_MAX_HISTORY_STEPS = 6
_HISTORY_THOUGHT_CHARS = 240
_HISTORY_MSG_CHARS = 160
# DomState.to_prompt() 已做头+尾截断（12K 文本 + 元素清单），这里兜底放宽到
# 28K，避免这一层反而把观察截瞎。deepseek 上下文 64K+，撑得住。
_DOM_PROMPT_CHARS = 28000

# markdown 代码围栏：```json ... ``` 或 ``` ... ```
_FENCE_RE = re.compile(r"```(?:json|javascript|js)?\s*(.*?)```", re.IGNORECASE | re.DOTALL)
# 从一坨文本里抠出第一个平衡的 JSON 对象
_FIRST_OBJ_RE = re.compile(r"\{.*\}", re.DOTALL)

# 键名同义词修复（模型偶尔会换个说法）
_THOUGHT_KEYS = ("thought", "reasoning", "reason", "observation", "think")
_ACTION_KEYS = ("action", "act", "tool", "command")
_NAME_KEYS = ("name", "action", "tool", "type", "command")
_ARGS_KEYS = ("args", "arguments", "parameters", "params", "input")


class Planner:
    """DeepSeek 规划器：决策下一步动作 + 结构化抽取。"""

    def __init__(
        self,
        model: str | None = None,
        base_url: str = "https://api.deepseek.com",
        api_key: str | None = None,
        prompts_module=None,
    ) -> None:
        # 本账号 /models 实测只有 deepseek-v4-flash / deepseek-v4-pro
        # （legacy deepseek-chat 已不在列表，2026-07-24 正式退役），
        # 故默认 v4-flash；可用环境变量 DEEPSEEK_MODEL 或 CLI 菜单覆盖。
        # §7A-C2：brain 包里没有 agent.prompts，fallback 已删——必须显式注入
        # 提示词模块（macOS 路径传 macos.prompts）。先校验再建 client，
        # 让配置错误干净地抛 ValueError，而不是被 client 构造的报错盖住。
        if prompts_module is None:
            raise ValueError(
                "Planner 需要显式传入 prompts_module（如 macos.prompts），"
                "brain 包内不再有默认提示词模块（spec §7A-C2）"
            )
        self.prompts = prompts_module

        self.model = model or os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")
        self.base_url = base_url
        self._api_key = api_key or os.getenv("DEEPSEEK_API_KEY")
        # VM 内 api_key 是 broker 的 bearer token（必填）；但为了让本类在无
        # 网络/无 key 的单测里也能构造（只测校验/修复纯逻辑），给个占位符。
        # AsyncOpenAI 只在真正发请求时才用到 key，占位不影响离线测试。
        self.client = AsyncOpenAI(
            api_key=self._api_key or "placeholder-no-key", base_url=base_url,
        )

    # ------------------------------------------------------------------ #
    # 决策
    # ------------------------------------------------------------------ #
    async def decide(self, goal: str, dom_prompt: str, history: list) -> dict:
        """返回 {"thought": str, "action": {"name": str, "args": dict}}。

        history: 形如 [{"thought","action","result"}...] 的步骤列表
                 （由 ReActAgent 维护）；这里只取最近若干步做上下文。
        无论模型返回什么，本方法都保证返回结构合法、动作名合法、args 类型正确。
        """
        user_content = self._build_user_message(goal, dom_prompt, history)
        messages = [
            {"role": "system", "content": self.prompts.get_system_prompt()},
            {"role": "user", "content": user_content},
        ]

        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                response_format={"type": "json_object"},
                messages=messages,
                temperature=0.0,
                # v4 是推理模型：思考(reasoning)与正文共享 max_tokens。
                # 1024 会在思考较长时把正文 JSON 掐断（实测出现过 "name": "don 截断），
                # 触发修复层降级成 wait 白烧一步，连续 3 次会中止任务。
                max_tokens=8192,
            )
            raw = response.choices[0].message.content or ""
        except Exception as e:  # 网络/额度/服务端错误 —— 不让整个循环崩掉
            return {
                "thought": f"规划器调用 DeepSeek 失败：{e}，先等待 2 秒后重试。",
                "action": {"name": "wait", "args": {"seconds": 2}},
                "_error": str(e),
            }

        parsed = self._parse_json(raw)
        return self._validate_and_repair(parsed, raw)

    # ------------------------------------------------------------------ #
    # 抽取（复用独立 utils 实现）
    # ------------------------------------------------------------------ #
    async def extract(self, page_text: str, schema: str) -> dict:
        """调用 utils 模块做结构化抽取。

        §7A-C3：base_url / api_key 必须穿透——不传的话 utils 会用字面量
        直打 api.deepseek.com，VM 内（无真 key）每次 extract 都失败，
        A3、A5 auto-extract 全挂。
        """
        return await extract_information_json(
            page_text,
            schema,
            model=self.model,
            base_url=self.base_url,
            api_key=self._api_key,
        )

    # ================================================================== #
    # 内部：构造消息
    # ================================================================== #
    def _build_user_message(self, goal: str, dom_prompt: str, history: list) -> str:
        dom_prompt = (dom_prompt or "")[:_DOM_PROMPT_CHARS]
        parts = [f"# GOAL（任务目标）\n{goal}", ""]

        hist_text = self._format_history(history)
        if hist_text:
            parts.append("# HISTORY（最近几步，越靠后越新）")
            parts.append(hist_text)
            parts.append("")

        parts.append("# CURRENT PAGE（当前观察：窗口控件清单与可见文本）")
        parts.append(dom_prompt)
        parts.append("")
        parts.append(
            "请只输出规定的 JSON：{\"thought\": ..., \"action\": {\"name\": ..., \"args\": {...}}}。"
        )
        return "\n".join(parts)

    def _format_history(self, history: list) -> str:
        if not history:
            return ""
        recent = history[-_MAX_HISTORY_STEPS:]
        lines: list[str] = []
        base = max(0, len(history) - len(recent))
        for offset, step in enumerate(recent):
            i = base + offset + 1
            if not isinstance(step, dict):
                lines.append(f"[{i}] {str(step)[:_HISTORY_MSG_CHARS]}")
                continue
            # 事件型条目（recovery / observe_error 等）也让模型看到
            if "event" in step and "action" not in step:
                detail = str(step.get("detail", ""))[:_HISTORY_MSG_CHARS]
                lines.append(f"[{i}] 事件: {step['event']} {detail}")
                continue
            thought = str(step.get("thought", ""))[:_HISTORY_THOUGHT_CHARS]
            action = step.get("action")
            action_str = self._compact_action(action)
            result = step.get("result")
            result_str = self._compact_result(result)
            seg = f"[{i}] 想法: {thought} | 动作: {action_str}"
            if result_str:
                seg += f" | 结果: {result_str}"
            lines.append(seg)
        return "\n".join(lines)

    @staticmethod
    def _compact_action(action: Any) -> str:
        if isinstance(action, dict):
            name = action.get("name", "?")
            args = action.get("args", {})
            try:
                args_str = json.dumps(args, ensure_ascii=False)
            except Exception:
                args_str = str(args)
            return f"{name}{args_str}"
        return str(action)[:_HISTORY_MSG_CHARS]

    @staticmethod
    def _compact_result(result: Any) -> str:
        if result is None:
            return ""
        if isinstance(result, dict):
            # ActionResult 可能被序列化成 dict
            ok = result.get("ok")
            msg = result.get("message", "")
            prefix = "" if ok is None else ("成功: " if ok else "失败: ")
            return (prefix + str(msg))[:_HISTORY_MSG_CHARS]
        return str(result)[:_HISTORY_MSG_CHARS]

    # ================================================================== #
    # 内部：解析 + 校验 + 修复
    # ================================================================== #
    @staticmethod
    def _parse_json(raw: str) -> Any:
        """把模型返回文本解析成 Python 对象，尽力对付脏输出。"""
        if raw is None:
            return None
        text = raw.strip()
        if not text:
            return None

        # 1) 直接尝试
        try:
            return json.loads(text)
        except Exception:
            pass

        # 2) 剥掉 markdown 代码围栏
        m = _FENCE_RE.search(text)
        if m:
            inner = m.group(1).strip()
            try:
                return json.loads(inner)
            except Exception:
                text = inner  # 继续用去围栏后的内容往下试

        # 3) 抠出第一个 {...} 块
        m = _FIRST_OBJ_RE.search(text)
        if m:
            candidate = m.group(0)
            try:
                return json.loads(candidate)
            except Exception:
                # 4) 去掉行尾多余逗号再试一次
                fixed = re.sub(r",\s*([}\]])", r"\1", candidate)
                try:
                    return json.loads(fixed)
                except Exception:
                    return None
        return None

    def _validate_and_repair(self, parsed: Any, raw: str) -> dict:
        """把任意解析结果修成 {"thought","action":{"name","args"}}。"""
        if not isinstance(parsed, dict):
            return self._fallback(
                f"模型返回无法解析为 JSON 对象，原始片段: {str(raw)[:120]}"
            )

        thought = self._pick(parsed, _THOUGHT_KEYS, default="")
        if not isinstance(thought, str):
            thought = str(thought)

        action_obj = self._pick(parsed, _ACTION_KEYS, default=None)

        # 情况 A：action 是对象
        if isinstance(action_obj, dict):
            name = self._pick(action_obj, _NAME_KEYS, default=None)
            args = self._pick(action_obj, _ARGS_KEYS, default=None)
            if args is None:
                # {"action": {"name": "extract"}, "args": {...}}：参数漏写在
                # 顶层——和情况 C 一样把顶层 args 捡回来，别丢 schema
                top_args = self._pick(parsed, _ARGS_KEYS, default=None)
                if isinstance(top_args, dict):
                    args = top_args
        # 情况 B：整个对象本身就带 name/args（模型漏了 action 外层）
        elif action_obj is None and self._pick(parsed, _NAME_KEYS, default=None):
            name = self._pick(parsed, _NAME_KEYS, default=None)
            args = self._pick(parsed, _ARGS_KEYS, default=None)
        # 情况 C：action 是个字符串，如 "click" 或 'click(3)'
        elif isinstance(action_obj, str):
            name, args = self._parse_action_string(action_obj)
            # 形如 {"action": "extract", "args": {...}}：动作名在 action、
            # 参数在顶层 args——把顶层 args 合并进来，否则 schema 等参数被丢掉
            top_args = self._pick(parsed, _ARGS_KEYS, default=None)
            if isinstance(top_args, dict):
                args = {**top_args, **args}
        else:
            return self._fallback(
                f"缺少可识别的 action 字段（thought='{thought[:60]}'）"
            )

        return self._finalize(thought, name, args, raw)

    def _finalize(self, thought: str, name: Any, args: Any, raw: str) -> dict:
        # 规范化动作名
        if not isinstance(name, str):
            return self._fallback(f"动作名不是字符串: {name!r}")
        name = name.strip()
        # 有时模型会写成 "actions.click" / "click()" 之类
        name = name.split("(")[0].split(".")[-1].strip().lower()

        if name not in self.prompts.ACTION_NAMES:
            alias = self._alias(name)
            # alias 也必须落在当前动作空间里：macOS 版没有 go_to/switch_tab，
            # "goto"→"go_to" 这类映射若不复查会在下面 ACTION_SPEC[name] 处 KeyError。
            if alias and alias in self.prompts.ACTION_NAMES:
                name = alias
            else:
                return self._fallback(
                    f"未知动作 '{name}'，合法动作为: {sorted(self.prompts.ACTION_NAMES)}"
                )

        # 规范化 args
        if args is None:
            args = {}
        if not isinstance(args, dict):
            # 允许 args 是标量/列表：塞进该动作的第一个必填位
            args = self._coerce_scalar_args(name, args)

        args = self._coerce_arg_types(name, dict(args))

        # 必填项检查
        missing = [k for k in self.prompts.ACTION_SPEC[name]["required"] if k not in args]
        if missing:
            return self._fallback(
                f"动作 '{name}' 缺少必填参数 {missing}，已得到 args={args}"
            )

        return {"thought": thought, "action": {"name": name, "args": args}}

    # ---- 辅助小工具 ---------------------------------------------------- #
    @staticmethod
    def _pick(d: dict, keys: tuple, default=None):
        for k in keys:
            if k in d and d[k] is not None:
                return d[k]
        # 不区分大小写再兜一次
        lower = {str(k).lower(): v for k, v in d.items()}
        for k in keys:
            if k in lower and lower[k] is not None:
                return lower[k]
        return default

    @staticmethod
    def _alias(name: str) -> str | None:
        aliases = {
            "navigate": "go_to",
            "goto": "go_to",
            "open": "launch_app",
            "launch": "launch_app",
            "open_app": "launch_app",
            "start_app": "launch_app",
            "back": "go_back",
            "goback": "go_back",
            "input": "type",
            "fill": "type",
            "enter_text": "type",
            "write": "type",
            "choose": "select",
            "dropdown": "select",
            "tab": "switch_tab",
            "switchtab": "switch_tab",
            "key": "press_key",
            "presskey": "press_key",
            "keypress": "press_key",
            "sleep": "wait",
            "finish": "done",
            "complete": "done",
            "stop": "done",
            "end": "done",
            "scrape": "extract",
            "get_data": "extract",
            "close": "close_window",
            "closewindow": "close_window",
        }
        return aliases.get(name)

    @staticmethod
    def _parse_action_string(s: str) -> tuple[str, dict]:
        """把 'click(3)' / 'go_to(https://x)' 解析成 (name, args)。"""
        s = s.strip()
        m = re.match(r"([a-zA-Z_]+)\s*\((.*)\)\s*$", s, re.DOTALL)
        if not m:
            return s.split("(")[0].strip(), {}
        name = m.group(1).strip().lower()
        inner = m.group(2).strip()
        args: dict = {}
        if inner:
            # 只做最常见的单参数猜测
            if name in ("click", "switch_tab") and inner.lstrip("-").isdigit():
                args["index"] = int(inner)
            elif name == "go_to":
                args["url"] = inner.strip("'\"")
            elif name == "press_key":
                args["key"] = inner.strip("'\"")
            elif name == "wait":
                try:
                    args["seconds"] = float(inner)
                except ValueError:
                    args["seconds"] = 2
        return name, args

    def _coerce_scalar_args(self, name: str, value: Any) -> dict:
        """args 是标量/列表时，映射到该动作最关键的参数。"""
        required = self.prompts.ACTION_SPEC[name]["required"]
        if isinstance(value, list):
            # 按必填顺序对号入座
            return {k: v for k, v in zip(required, value)}
        if not required:
            return {}
        # 单标量 -> 第一个必填参数
        return {required[0]: value}

    @staticmethod
    def _coerce_arg_types(name: str, args: dict) -> dict:
        """把常见的类型写错（index='3'、submit='true'）纠正过来。"""

        def to_int(v):
            if isinstance(v, bool):
                return v
            if isinstance(v, int):
                return v
            if isinstance(v, float) and v.is_integer():
                return int(v)
            if isinstance(v, str):
                m = re.search(r"-?\d+", v)
                if m:
                    return int(m.group(0))
            return v  # 交给上层必填校验/executor 处理

        def to_bool(v):
            if isinstance(v, bool):
                return v
            if isinstance(v, str):
                return v.strip().lower() in ("true", "yes", "y", "1", "submit")
            if isinstance(v, (int, float)):
                return bool(v)
            return v

        def to_num(v):
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                return v
            if isinstance(v, str):
                m = re.search(r"-?\d+(?:\.\d+)?", v)
                if m:
                    return float(m.group(0))
            return v

        if "index" in args:
            args["index"] = to_int(args["index"])
        if name == "type" and "submit" in args:
            args["submit"] = to_bool(args["submit"])
        if name == "select" and "value" in args and not isinstance(args["value"], str):
            args["value"] = str(args["value"])
        if name == "scroll":
            if "direction" in args and isinstance(args["direction"], str):
                args["direction"] = args["direction"].strip().lower()
            if "amount" in args:
                args["amount"] = to_int(args["amount"])
        if name == "wait" and "seconds" in args:
            args["seconds"] = to_num(args["seconds"])
        if name == "done" and "success" in args:
            args["success"] = to_bool(args["success"])
        if name == "go_to" and "url" in args and not isinstance(args["url"], str):
            args["url"] = str(args["url"])
        if name == "press_key" and "key" in args and not isinstance(args["key"], str):
            args["key"] = str(args["key"])
        if name == "launch_app" and "app" in args and not isinstance(args["app"], str):
            args["app"] = str(args["app"])
        return args

    @staticmethod
    def _fallback(reason: str) -> dict:
        """无法修复时的安全降级：等待 1 秒，让上层循环得以继续并触发重观察。

        用 wait 而非 done，是为了不误判任务失败——给模型下一步凭新的 DOM 纠错的机会。
        """
        return {
            "thought": f"（自动修复）未能解析出合法动作：{reason}。先等待 1 秒后重新观察页面。",
            "action": {"name": "wait", "args": {"seconds": 1}},
            "_repair": reason,
        }
