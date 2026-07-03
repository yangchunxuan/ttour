"""company.eyes —— 「读原生 App 的眼睛」(从已放弃的 macos_agent 引擎里保留下来的可复用件)。

只留读取能力,不带任何 VM/隔离逻辑:
  * ax.py     —— macOS 辅助功能(AX)+ CGEvent 原语(读控件树、前台 App、发按键)。
  * observe.py —— 把某个 App 当前窗口的控件读成结构化 MacDomState(将来读微信等原生 App 用)。
纯 pyobjc,无内部依赖;observe(session) 的 session 参数不再被使用,可传任意占位对象。
"""
