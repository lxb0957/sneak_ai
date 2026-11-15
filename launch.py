"""打包入口 —— 兜住输出，再叫起包内的 main()。

为什么不能直接拿 `sneak_ai/__main__.py` 当打包入口：
PyInstaller 是**按脚本**执行的，`python sneak_ai/__main__.py` 和
`python -m sneak_ai` 在语义上并不一样。前者把文件当成顶层脚本，
`__package__` 是空字符串，于是 `__main__.py` 里那一堆
`from . import constants as C` 全部炸掉：

    ImportError: attempted relative import with no known parent package

这个异常发生在**导入阶段**，还没进 main()，所以连 crash.log 都写不出来 ——
双击 exe 看到的就是窗口闪一下。用 `python -m sneak_ai` 测一百次都测不出来，
因为那条路 `__package__ == "sneak_ai"`。

根目录放一个 launcher 是最省事的解法：它是顶层脚本，用**绝对导入**
把包拉进来，包的相对导入随之恢复正常。`python -m sneak_ai` 那条路
仍然由 `__main__.py` 里的 `if __name__ == "__main__"` 兜着，两边都能跑。

## 为什么这里要兜 stdout / stderr

exe 用 `--windowed` 打包（双击不弹黑框）。Windows 下这种进程**没有控制台**，
`sys.stdout` 和 `sys.stderr` 都是 `None` —— 而项目里有 17 处 `print`，
全在诊断路径上（自检结果、崩溃提示、关卡体检）。`print` 撞上 `None`
会抛 `AttributeError: 'NoneType' object has no attribute 'write'`，
所以"去掉黑框"这件事如果不处理，会变成"双击之后什么都没发生"。

修法不是删掉那些 print（它们是排查问题的唯一线索），而是把它们接到
exe 旁边的 `runtime.log`。同时装一个 excepthook：导入阶段就崩的异常
（就是上面那个 ImportError 的场景）也能落进同一个文件 ——
`--console` 时代靠"控制台闪一下"能看见的东西，现在在文件里看。

这段逻辑放在 `launch.py` 而不是 `sneak_ai/__main__.py`，是因为它必须
**先于 `import sneak_ai`** 执行；放进包里就晚了。
"""

import os
import sys
import traceback
from pathlib import Path


def _log_path() -> Path:
    """和 records.json / settings.json 同一个目录：打包后在 exe 旁边。

    这里刻意不复用 `records._frozen_data_dir()` —— 那要 `import sneak_ai`，
    而本文件存在的意义就是兜住 `import sneak_ai` 失败的情况。三行逻辑再写一遍，
    好过让兜底依赖被兜底的东西。
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "runtime.log"
    return Path(__file__).resolve().parent / "runtime.log"


def _ensure_streams() -> None:
    """没有控制台时把 stdout / stderr 接到 runtime.log；有控制台就原样不动。"""
    if sys.stdout is not None and sys.stderr is not None:
        return
    try:
        log = open(_log_path(), "a", encoding="utf-8", errors="replace", buffering=1)
    except OSError:
        # 只读目录 / 权限不足：连日志都开不了，退到 null。
        # 宁可静音，也绝不能让"写日志"本身成为崩溃原因。
        null = open(os.devnull, "w", encoding="utf-8")
        sys.stdout = sys.stdout or null
        sys.stderr = sys.stderr or null
        return
    if sys.stdout is None:
        sys.stdout = log
    if sys.stderr is None:
        sys.stderr = log


def _install_crash_hook() -> None:
    """未捕获异常（含导入阶段）写进 stderr 指向的地方。"""

    def hook(exc_type, exc, tb) -> None:
        try:
            traceback.print_exception(exc_type, exc, tb, file=sys.stderr)
            if sys.stderr is not None:
                sys.stderr.flush()
        except Exception:
            # 崩溃处理器自己崩了也只能算了，但绝不能让它盖住原始异常
            pass

    sys.excepthook = hook


_ensure_streams()
_install_crash_hook()

from sneak_ai.__main__ import main  # noqa: E402  (必须排在兜底之后)

if __name__ == "__main__":
    sys.exit(main())
