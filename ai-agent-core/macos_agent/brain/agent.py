"""brain/agent.py

The ReAct control loop.

    observe -> planner.decide -> execute -> (re-observe) ... until done / cap.

macOS VM 版（§7A）：
  * 原网页版顶部的 `from agent.dom/.actions/.health import …` 会连带拉起
    Playwright —— 这里改成懒加载（导入失败置 None）。macOS 路径必须显式
    注入 observe_fn / execute_fn / health_fn / dismiss_popups_fn 四个函数，
    __init__ 里对 None 直接报错，不依赖任何 Playwright 默认值。
  * loop_guards 改为包内相对导入。
  * 其余循环逻辑与网页版逐行一致（勿动，A8 大脑零回归）。

The loop adds, beyond the bare ReAct cycle:
  * step cap (max_steps)
  * WINDOW-based loop detection: a signature seen twice within the last few
    steps (catches A-B-A-B oscillation like click<->go_back, not just A-A),
    plus a "only 2 distinct moves in 4+ steps" oscillation check
  * escalating recovery: hint -> forced recovery (popups/go_back) -> abort
    after 3 recoveries with no progress (never burns max_steps ping-ponging)
  * hallucinated-index guard: click/type/select with an index that is not in
    the current observation is rejected BEFORE touching the browser
  * extract discipline: a successful extract injects a "now call done" hint;
    an identical repeat extract auto-finishes; 3+ consecutive extracts are cut
  * planner-fallback circuit breaker: 3 consecutive unparseable/errored
    decisions abort the run instead of spinning on wait()
  * self-correction: every step's ok/message is written to entry["result"],
    which Planner._format_history renders back to the model next round
  * a structured transcript, optional on_step callback, and AgentResult
"""

from __future__ import annotations

import hashlib
import json
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Optional
from urllib.parse import urlparse, urljoin

# §7A：网页版默认实现（Playwright 系）按懒加载处理——导入失败一律置 None，
# macOS 路径强制从外部注入，绝不隐式依赖这些默认值。
try:  # pragma: no cover - 仅在装了网页版依赖的环境里才会成功
    from agent.dom import observe as default_observe  # type: ignore
    from agent.actions import (  # type: ignore
        execute as default_execute,
        dismiss_popups as default_dismiss_popups,
    )
    from agent.health import assess_page_health as default_health  # type: ignore
except Exception:  # noqa: BLE001 - 无 Playwright 环境（macOS VM）是常态
    default_observe = None
    default_execute = None
    default_dismiss_popups = None
    default_health = None

from .loop_guards import (
    _action_signature,
    _is_error_payload,
    _page_fingerprint,
    _looks_like_closed_browser,
    _blocked_go_to_reason,
    _host,
    _site_key,
    _same_site,
    _normalize_url,
    _arg_success,
    _page_has_observable_content,
    _goal_needs_data,
    _done_result_too_thin,
    _blocked_type_reason,
)


# Loop-detection window: how many recent action signatures we remember.
_SIG_WINDOW = 6
# Forced recoveries allowed before declaring the run stuck.
_MAX_RECOVERIES = 3
# Consecutive planner fallbacks (unparseable/API-error decisions) tolerated.
_MAX_FALLBACK_STREAK = 3
# Consecutive extract attempts tolerated before we force a resolution.
_MAX_EXTRACT_STREAK = 3

_INDEX_ACTIONS = ("click", "type", "select")

_CLOSED_BROWSER_MARKERS = (
    "target page, context or browser has been closed",
    "browser has been closed",
    "context has been closed",
    "page has been closed",
    "has no page",
)


@dataclass
class AgentResult:
    success: bool
    result: Optional[dict]
    transcript: list = field(default_factory=list)
    steps: int = 0
    message: str = ""




class ReActAgent:
    def __init__(
        self,
        session,
        planner,
        max_steps: int = 20,
        observe_fn=default_observe,
        execute_fn=default_execute,
        health_fn=default_health,
        dismiss_popups_fn=default_dismiss_popups,
    ):
        # §7A：macOS 环境下四个默认值全是 None——调用方必须显式注入。
        missing = [
            name for name, fn in (
                ("observe_fn", observe_fn),
                ("execute_fn", execute_fn),
                ("health_fn", health_fn),
                ("dismiss_popups_fn", dismiss_popups_fn),
            ) if fn is None
        ]
        if missing:
            raise ValueError(
                "ReActAgent 缺少必须注入的函数: "
                + ", ".join(missing)
                + "（本环境无 Playwright 默认实现，见 spec §7A）"
            )
        self.session = session
        self.planner = planner
        self.max_steps = max_steps
        self.observe_fn = observe_fn
        self.execute_fn = execute_fn
        self.health_fn = health_fn
        self.dismiss_popups_fn = dismiss_popups_fn

    async def run(
        self,
        goal: str,
        start_url: str,
        on_step: Optional[Callable[[dict], None]] = None,
    ) -> AgentResult:
        transcript: list = []

        def emit(entry: dict) -> None:
            transcript.append(entry)
            if on_step is not None:
                try:
                    on_step(entry)
                except Exception:
                    pass  # a broken UI callback must never kill the run

        # --- setup: login (headful if needed) then navigate to the start URL ---
        try:
            await self.session.ensure_login(start_url)
        except Exception as e:  # noqa: BLE001
            emit({"step": 0, "event": "login_error", "detail": str(e)})
        # ensure_login may already be on the page; goto guarantees we land there.
        try:
            await self.session.goto(start_url)
        except Exception as e:  # noqa: BLE001
            emit({"step": 0, "event": "goto_error", "detail": str(e)})

        recent_sigs: deque = deque(maxlen=_SIG_WINDOW)
        recovery_count = 0
        fallback_streak = 0
        extract_streak = 0
        # 守卫拒绝（幻觉编号 / 被拦的 type / 被拦的 go_to）连击计数：这些分支
        # continue 后到不了循环检测，不计数的话模型可以每步被拒一次烧光步数。
        guard_reject_streak = 0
        # 对同一 (index, text) 的 type 只硬拦一次；再次坚持就放行——守卫只拦
        # 无意识编值，不该永久卡死跨语言检索词这类合理派生输入。
        warned_type_attempts: set = set()
        premature_fail_done_count = 0
        premature_success_done_count = 0
        post_extract_fail_done_count = 0
        final_extracted: Optional[dict] = None
        last_success_extract_sig: Optional[str] = None
        last_success_page_fp: Optional[str] = None
        pending_hint: Optional[str] = None
        last_dom_state = None

        # The model needs to know where "home" is: after a bad go_back chain or
        # an error page (500 etc.), go_to START_URL beats wandering to guessed
        # domains or waiting in place.
        goal_ctx = (
            f"{goal}\n\n[START_URL] {start_url}\n"
            "（重要：如果迷路、页面空白、或遇到 500/404 等错误页，"
            "不要反复 wait，直接用 go_to 回到 START_URL 重新出发。）"
        )

        for step in range(1, self.max_steps + 1):
            # 1) OBSERVE ------------------------------------------------------
            try:
                if await self.session.ensure_alive(start_url):
                    emit({
                        "step": step,
                        "event": "recovery",
                        "detail": f"browser/page was closed; restarted at {start_url}",
                    })
                dom_state = await self.observe_fn(self.session)
                last_dom_state = dom_state
            except Exception as e:  # noqa: BLE001
                emit({"step": step, "event": "observe_error", "detail": str(e)})
                await self._recover(emit, step, start_url)
                continue

            page_health = self.health_fn(dom_state)
            if not page_health.ok:
                emit({
                    "step": step,
                    "event": "page_health",
                    "detail": page_health.to_hint(),
                    "signals": page_health.signals,
                })
                if page_health.bad:
                    auto_extracted = await self._auto_extract_if_possible(
                        emit, step, dom_state, goal
                    )
                    if auto_extracted is not None:
                        return self._finish(
                            True, auto_extracted, transcript, step,
                            f"auto-extracted despite page health: {page_health.reason}",
                        )
                    return self._finish(
                        False, final_extracted, transcript, step,
                        f"page unusable: {page_health.reason}",
                    )

            dom_prompt = dom_state.to_prompt()
            if not page_health.ok:
                dom_prompt = f"[{page_health.to_hint()}]\n\n{dom_prompt}"
            if pending_hint:
                dom_prompt = f"[HINT] {pending_hint}\n\n{dom_prompt}"
                pending_hint = None

            # 2) DECIDE -------------------------------------------------------
            try:
                decision = await self.planner.decide(goal_ctx, dom_prompt, transcript)
            except Exception as e:  # noqa: BLE001
                emit({"step": step, "event": "decide_error", "detail": str(e)})
                pending_hint = "The previous planning call failed; return a valid JSON action."
                continue

            # Circuit breaker: the planner's repair layer marks decisions it had
            # to fabricate (unparseable output / API error) with _repair/_error.
            if "_repair" in decision or "_error" in decision:
                fallback_streak += 1
                if fallback_streak >= _MAX_FALLBACK_STREAK:
                    return self._finish(
                        False, final_extracted, transcript, step,
                        "aborted: planner returned unusable output "
                        f"{fallback_streak} times in a row",
                    )
            else:
                fallback_streak = 0

            thought = str(decision.get("thought", ""))
            action = decision.get("action") or {}
            name = action.get("name", "")
            args = action.get("args") or {}
            # Element text goes into the signature so the same index number on
            # a DIFFERENT page/element never counts as a repeat.
            el_text = ""
            if name in _INDEX_ACTIONS:
                try:
                    _el = dom_state.get(int(args.get("index")))
                    el_text = _el.text if _el is not None else ""
                except (TypeError, ValueError):
                    pass
            sig = _action_signature(action, dom_state.url, el_text)

            entry = {
                "step": step,
                "url": dom_state.url,
                "title": dom_state.title,
                "thought": thought,
                "action": action,
            }

            # 3) HALLUCINATED-INDEX GUARD --------------------------------------
            # Reject element actions whose index is not in THIS observation
            # before they hit the browser and waste a doomed round-trip.
            if name in _INDEX_ACTIONS:
                try:
                    idx = int(args.get("index"))
                except (TypeError, ValueError):
                    idx = None
                if idx is None or dom_state.get(idx) is None:
                    valid_max = max((el.index for el in dom_state.elements), default=-1)
                    msg = (
                        f"invalid index {args.get('index')!r}: not in the current "
                        f"element list (valid: 0..{valid_max})"
                    )
                    entry["result"] = {"ok": False, "message": msg}
                    entry["result_ok"] = False
                    entry["result_message"] = msg
                    emit(entry)
                    pending_hint = (
                        f"你上一步用了不存在的编号 {args.get('index')!r}。只能使用当前 DOM "
                        f"列表里方括号中的编号（0..{valid_max}），重新阅读列表后再选。"
                    )
                    recent_sigs.append(sig)
                    guard_reject_streak += 1
                    if guard_reject_streak >= 3:
                        guard_reject_streak = 0
                        recovery_count += 1
                        if recovery_count > _MAX_RECOVERIES:
                            return self._finish(
                                final_extracted is not None, final_extracted,
                                transcript, step,
                                "aborted: planner kept emitting rejected "
                                "actions with no progress",
                            )
                        await self._recover(emit, step, start_url)
                    continue

            if name == "type":
                blocked_reason = _blocked_type_reason(args, dom_state, goal)
                type_key = (args.get("index"), str(args.get("text", "")))
                if blocked_reason and type_key in warned_type_attempts:
                    # 警告过一次仍坚持同样的输入：放行。硬拦第二次会把
                    # "目标是中文、页面要英文检索词"这类合理派生输入永久卡死。
                    blocked_reason = ""
                if blocked_reason:
                    warned_type_attempts.add(type_key)
                    entry["result"] = {"ok": False, "message": blocked_reason}
                    entry["result_ok"] = False
                    entry["result_message"] = blocked_reason
                    emit(entry)
                    pending_hint = (
                        f"{blocked_reason}. Do not invent form values. If the "
                        "goal lacks required fields, extract visible information "
                        "or name the exact missing inputs instead. If the value "
                        "is genuinely derived from the goal, you may retry it once."
                    )
                    recent_sigs.append(sig)
                    guard_reject_streak += 1
                    if guard_reject_streak >= 3:
                        guard_reject_streak = 0
                        recovery_count += 1
                        if recovery_count > _MAX_RECOVERIES:
                            return self._finish(
                                final_extracted is not None, final_extracted,
                                transcript, step,
                                "aborted: planner kept emitting rejected "
                                "actions with no progress",
                            )
                        await self._recover(emit, step, start_url)
                    continue

            # Guard against the planner inventing unrelated sites. It may use
            # go_to for START_URL recovery or for a URL that is actually present
            # in the current page, but not for free-form domain guessing.
            if name == "go_to":
                target_url = args.get("url") if isinstance(args, dict) else None
                blocked_reason = _blocked_go_to_reason(target_url, start_url, dom_state)
                if blocked_reason:
                    entry["result"] = {"ok": False, "message": blocked_reason}
                    entry["result_ok"] = False
                    entry["result_message"] = blocked_reason
                    emit(entry)
                    pending_hint = (
                        f"{blocked_reason}. Use a visible link's index, switch_tab, "
                        "or go_to START_URL/current-site URLs only."
                    )
                    recent_sigs.append(sig)
                    guard_reject_streak += 1
                    if guard_reject_streak >= 3:
                        guard_reject_streak = 0
                        recovery_count += 1
                        if recovery_count > _MAX_RECOVERIES:
                            return self._finish(
                                final_extracted is not None, final_extracted,
                                transcript, step,
                                "aborted: planner kept emitting rejected "
                                "actions with no progress",
                            )
                        await self._recover(emit, step, start_url)
                    continue

            # 4) LOOP DETECTION (window-based) ---------------------------------
            seen_before = sum(1 for s in recent_sigs if s == sig)
            oscillating = (
                len(recent_sigs) >= 4 and len(set(recent_sigs)) <= 2 and sig in recent_sigs
            )
            recent_sigs.append(sig)

            # done is always allowed; extract has its own discipline below.
            if name not in ("extract", "done") and (seen_before >= 2 or oscillating):
                entry["forced_recovery"] = True
                msg = "loop detected: this move (or a 2-move oscillation) keeps repeating"
                entry["result"] = {"ok": False, "message": msg}
                entry["result_ok"] = False
                entry["result_message"] = msg
                emit(entry)

                recovery_count += 1
                # 用 > 而非 >=：数到 3 就中止意味着第 3 次恢复从未执行过，
                # 实际只恢复了 2 次就放弃。
                if recovery_count > _MAX_RECOVERIES:
                    auto_extracted = await self._auto_extract_if_possible(
                        emit, step, dom_state, goal
                    )
                    if auto_extracted is not None:
                        return self._finish(
                            True, auto_extracted, transcript, step,
                            "auto-extracted visible page data after repeated stalling",
                        )
                    return self._finish(
                        final_extracted is not None, final_extracted, transcript, step,
                        "aborted: stuck in a loop with no progress after "
                        f"{recovery_count} recovery attempts",
                    )
                await self._recover(emit, step, start_url)
                pending_hint = (
                    "检测到循环：刚才的动作组合反复出现却没有进展。禁止再用同样的动作，"
                    "必须换一条完全不同的路径（其他元素/滚动/切标签页/直接 go_to 已知 URL）。"
                )
                continue
            if name not in ("extract", "done") and seen_before == 1:
                # Second occurrence within the window: warn, but still execute —
                # the page may genuinely need a repeated move (e.g. scroll).
                pending_hint = (
                    "你在重复最近用过的动作。如果页面没有变化，目标可能被弹窗挡住、"
                    "在别的标签页或 iframe 里。考虑 press_key Escape、switch_tab、"
                    "scroll 或 go_back。"
                )

            # 5) EXTRACT DISCIPLINE --------------------------------------------
            if name == "extract":
                extract_streak += 1
                # Identical re-extract after a success: the data is already in
                # hand — finish instead of paying another full LLM pass.
                # 额外要求页面内容指纹一致：iframe/SPA 翻页时 URL 不变，只比
                # 签名会把"翻页后的重新抽取"误判成重复，拿第一页旧数据交差。
                if (
                    final_extracted is not None
                    and sig == last_success_extract_sig
                    and _page_fingerprint(dom_state) == last_success_page_fp
                ):
                    entry["result"] = {"ok": True, "message": "auto-done: extraction already succeeded"}
                    emit(entry)
                    return self._finish(
                        True, final_extracted, transcript, step,
                        "extraction repeated after success — finished with the existing result",
                    )
                if extract_streak > _MAX_EXTRACT_STREAK:
                    if final_extracted is not None:
                        emit(entry)
                        return self._finish(
                            True, final_extracted, transcript, step,
                            "extract attempted too many times — finished with the best result",
                        )
                    msg = "extract blocked: attempted 3+ times in a row without success"
                    entry["result"] = {"ok": False, "message": msg}
                    emit(entry)
                    pending_hint = (
                        "extract 已连续失败多次。目标数据可能还没显示在页面上——先导航/"
                        "滚动/等待让数据出现，或者用 done(success=false) 诚实结束。"
                    )
                    continue
            else:
                extract_streak = 0

            if (
                name == "done"
                and not _arg_success(args)
                and final_extracted is not None
                and post_extract_fail_done_count < 1
            ):
                post_extract_fail_done_count += 1
                msg = (
                    "done(false) rejected: extraction already succeeded; finish "
                    "with done(success=true, result=...) or name exact missing fields"
                )
                entry["result"] = {"ok": False, "message": msg}
                entry["result_ok"] = False
                entry["result_message"] = msg
                emit(entry)
                pending_hint = (
                    "You already have extracted data. If it satisfies the goal, "
                    "call done(success=true, message=..., result=<that data>). "
                    "Only fail if you can name exact missing required fields."
                )
                continue

            if (
                name == "done"
                and not _arg_success(args)
                and _goal_needs_data(goal)
                and _page_has_observable_content(dom_state)
                and premature_fail_done_count >= 2
            ):
                auto_extracted = await self._auto_extract_if_possible(
                    emit, step, dom_state, goal
                )
                if auto_extracted is not None:
                    return self._finish(
                        True, auto_extracted, transcript, step,
                        "auto-extracted visible page data after repeated premature failure",
                    )

            if (
                name == "done"
                and _arg_success(args)
                and final_extracted is None
                and premature_success_done_count < 2
                and _goal_needs_data(goal)
                and _done_result_too_thin(args)
            ):
                premature_success_done_count += 1
                msg = (
                    "premature done(success) rejected: the goal asks for data, "
                    "but the result is missing or too thin"
                )
                entry["result"] = {"ok": False, "message": msg}
                entry["result_ok"] = False
                entry["result_message"] = msg
                emit(entry)
                pending_hint = (
                    "The user asked for extracted/JSON data. Do not finish with "
                    "only status/ok. Use extract with a schema matching the goal, "
                    "or call done(success=true) with the requested concrete fields."
                )
                continue

            # Do not let the planner give up immediately on a loaded page. In
            # practice DeepSeek sometimes emits done(false, "goal unclear")
            # even when the observation contains plenty of extractable text.
            # Force at least one concrete attempt before accepting that answer.
            if (
                name == "done"
                and not _arg_success(args)
                and final_extracted is None
                and premature_fail_done_count < 2
                and _page_has_observable_content(dom_state)
            ):
                premature_fail_done_count += 1
                msg = (
                    "premature done(false) rejected: page is loaded; extract "
                    "visible information or identify a concrete blocker first"
                )
                entry["result"] = {"ok": False, "message": msg}
                entry["result_ok"] = False
                entry["result_message"] = msg
                emit(entry)
                pending_hint = (
                    "Do not stop with a vague failure. The page is loaded and "
                    f"has {len(dom_state.elements)} visible controls. If the "
                    "goal is broad, extract the best visible information now; "
                    "if something blocks the task, name the exact blocker."
                )
                continue

            # 6) EXECUTE --------------------------------------------------------
            result = await self.execute_fn(self.session, dom_state, action, self.planner)
            entry["result"] = {"ok": result.ok, "message": result.message}
            # kept for backward compatibility with earlier transcript readers
            entry["result_ok"] = result.ok
            entry["result_message"] = result.message
            emit(entry)

            # 有实际进展就清零恢复/拒绝计数：恢复配额只该约束"连续无进展"，
            # 长任务里零散触发几次循环检测不该累积成整单报废。
            # wait/scroll 无条件返回 ok、不算进展——否则"坏动作×2 + scroll"
            # 这种循环会永远重置计数，退化回烧光步数的老问题。
            if result.ok and name not in ("wait", "scroll"):
                recovery_count = 0
                guard_reject_streak = 0

            if result.extracted is not None and result.ok and not _is_error_payload(result.extracted):
                final_extracted = result.extracted
                last_success_extract_sig = sig
                last_success_page_fp = _page_fingerprint(dom_state)
                if not result.done:
                    pending_hint = (
                        "抽取成功，数据已拿到。你现在必须立刻用 "
                        "done(success=true, message=..., result=<抽取结果>) 结束任务，"
                        "不要再重复 extract 或做任何多余动作。"
                    )

            # self-correction: a failed step leaves its message in the transcript
            # (entry["result"]), which decide() renders next round. Add a nudge.
            if not result.ok:
                if _looks_like_closed_browser(result.message):
                    recovered = await self._recover(emit, step, start_url)
                    pending_hint = (
                        "The page/browser was closed and recovery was attempted. "
                        "Observe the fresh page and continue from START_URL."
                        if recovered else
                        f"Last action failed because the page/browser closed: {result.message}."
                    )
                    continue
                pending_hint = (
                    f"Last action failed: {result.message}. Re-read the element list and adjust."
                )

            # 7) DONE? ----------------------------------------------------------
            if result.done:
                # Don't lose an earlier successful extraction when the model
                # calls done without threading the result through.
                final = result.extracted
                if final is None or _is_error_payload(final):
                    final = final_extracted
                return self._finish(result.ok, final, transcript, step, result.message)

        # step cap hit without an explicit done()
        if last_dom_state is not None:
            auto_extracted = await self._auto_extract_if_possible(
                emit, self.max_steps, last_dom_state, goal
            )
            if auto_extracted is not None:
                return self._finish(
                    True,
                    auto_extracted,
                    transcript,
                    self.max_steps,
                    "auto-extracted visible page data at max_steps",
                )
        return self._finish(
            final_extracted is not None,
            final_extracted,
            transcript,
            self.max_steps,
            "max_steps reached without done()",
        )

    # ------------------------------------------------------------------ #
    def _finish(
        self, success: bool, result: Optional[dict], transcript: list,
        steps: int, message: str,
    ) -> AgentResult:
        """Uniform exit."""
        return AgentResult(
            success=success, result=result, transcript=transcript,
            steps=steps, message=message,
        )

    async def _recover(self, emit, step: int, start_url: str = "") -> bool:
        """Self-correction escape hatch delegated to the session."""
        if hasattr(self.session, "recover"):
            return await self.session.recover(emit, step, start_url, self.dismiss_popups_fn)
        return False

    async def _auto_extract_if_possible(self, emit, step: int, dom_state, goal: str):
        """Last-resort data extraction from the current observation.

        This is intentionally outside the model's action loop. If the planner
        keeps stalling while the page clearly contains data, we still deliver a
        useful result instead of burning steps on wait/click guesses.
        """
        if not _goal_needs_data(goal) or not _page_has_observable_content(dom_state):
            return None

        emit({
            "step": step,
            "event": "auto_extract",
            "detail": "planner stalled; extracting from visible page state directly",
        })
        try:
            element_lines = "\n".join(
                element.render() for element in getattr(dom_state, "elements", [])[:120]
            )
            source = (
                f"URL: {getattr(dom_state, 'url', '')}\n"
                f"TITLE: {getattr(dom_state, 'title', '')}\n\n"
                f"VISIBLE PAGE TEXT:\n{getattr(dom_state, 'page_text', '')}\n\n"
                f"VISIBLE INTERACTIVE ELEMENTS:\n{element_lines}"
            )
            schema = (
                "Return one JSON object that satisfies this user goal as well "
                "as possible using only the supplied page state. Do not invent "
                "form values or unavailable facts.\n\n"
                f"USER_GOAL:\n{goal}\n\n"
                "Include title and url when visible/requested. For visible "
                "entry points, return an array of concise strings."
            )
            result = await self.planner.extract(source, schema)
        except Exception as e:  # noqa: BLE001
            emit({"step": step, "event": "auto_extract_error", "detail": str(e)})
            return None

        if result is None or _is_error_payload(result):
            emit({"step": step, "event": "auto_extract_error", "detail": str(result)})
            return None
        return result
