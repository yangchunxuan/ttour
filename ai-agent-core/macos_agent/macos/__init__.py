"""macos 包：macOS VM 桌面代理的「眼睛和手」。

  ax.py       所有 pyobjc / AX / CGEvent 调用的唯一出入口（懒加载，可打桩）
  guard.py    §2A.1 VM 守卫 + §2A.4 干净启动证明（fail-closed）
  observe.py  AX 控件树 -> MacDomState（对齐网页 DomState 契约）
  actions.py  执行器：click/type/... -> ActionResult
  prompts.py  桌面版动作空间 + 系统提示词（含 §2A.5 注入隔离前言）
  health.py   桌面版 health 桩（§7A-M1）
  session.py  MacSession（M6/M7 钉死的会话方法签名）

注意：这里刻意不 re-import 子模块——ax/observe 依赖 pyobjc，
在宿主跑单测（无 pyobjc）时按需导入并打桩。
"""
