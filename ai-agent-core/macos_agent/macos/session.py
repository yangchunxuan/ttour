"""macos/session.py — MacSession（spec §7A-M6/M7，方法签名钉死照抄）。

网页版 BrowserSession 的契约（agent.py 逐个调用）：
    ensure_login(start_url)          async
    goto(start_url)                  async
    ensure_alive(start_url)          async -> bool（返回是否刚做过恢复）
    recover(emit, step, start_url, dismiss_popups_fn) async -> bool

桌面没有"页面/浏览器被关闭"这回事——没有 URL 可导航、没有会话可登录。
所以这些方法多数是 no-op（返回合适的默认值让循环照常跑）。唯一有实质的是
recover()：清弹窗 + 尝试把目标应用带回前台。**recover 内任何真实按键前
先过守卫**（spec §7A-M7）——这里通过 dismiss_popups_fn（=actions.dismiss_popups，
已内建守卫）实现。

MacSession 还持有 guard（VmGuard 单例），供 actions.execute 取用。
"""

from __future__ import annotations

from typing import Callable, Optional

from .guard import get_guard


class MacSession:
    def __init__(self, guard=None, default_app: Optional[str] = None) -> None:
        # actions.execute 会 getattr(session, "guard", None) 取它过 §2A.2 守卫
        self.guard = guard if guard is not None else get_guard()
        # 恢复时想带回前台的应用（可选）；run_agent 可按任务设置
        self.default_app = default_app

    # ---- agent.py 在 run() 开头调用（桌面无登录/导航语义，no-op） ---- #
    async def ensure_login(self, start_url: str) -> None:
        return None

    async def goto(self, start_url: str) -> None:
        # 桌面没有"导航到 URL"；若指定了 default_app 就把它带到前台当起点。
        if self.default_app:
            try:
                from .actions import _do_launch_app  # 局部导入避免环依赖
                await _do_launch_app(self, None, {"app": self.default_app}, None)
            except Exception:
                pass
        return None

    async def ensure_alive(self, start_url: str) -> bool:
        # 桌面会话不会"被关闭"；永远活着，从没做过恢复。
        return False

    # ---- 恢复逃生口（spec §7A-M6：recover 内真实按键前先过守卫） ---- #
    async def recover(
        self,
        emit: Callable[[dict], None],
        step: int,
        start_url: str = "",
        dismiss_popups_fn: Optional[Callable] = None,
    ) -> bool:
        recovered = False
        # 1) 清弹窗（dismiss_popups_fn 内部已过守卫 —— §7A-M7）
        if dismiss_popups_fn is not None:
            try:
                recovered = bool(await dismiss_popups_fn(self))
            except Exception:
                recovered = False
        # 2) 把目标应用带回前台（launch_app 走守卫）
        if self.default_app:
            try:
                from .actions import _do_launch_app
                res = await _do_launch_app(self, None, {"app": self.default_app}, None)
                recovered = recovered or getattr(res, "ok", False)
            except Exception:
                pass
        emit({
            "step": step,
            "event": "recover",
            "detail": f"desktop recover (popups + refocus {self.default_app or 'n/a'})",
            "recovered": recovered,
        })
        return recovered
