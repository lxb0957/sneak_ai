"""W41：无控制台（windowed 打包）下不许静默死亡。

## 为什么需要这个

exe 用 `--windowed` 打包，双击不弹黑框 —— 但 Windows 上没有控制台的进程
`sys.stdout` 和 `sys.stderr` 都是 `None`。项目里有 17 处 `print`，
全在诊断路径上：自检结论、崩溃提示、关卡体检。

`print` 撞上 `None` 会抛：

    AttributeError: 'NoneType' object has no attribute 'write'

而这个异常发生在"本来只是想打印一行结果"的地方 —— 于是所有 `--selfcheck`
在打包后必然失败，且失败信息本身也写不出去。玩家双击看到的是
**窗口闪一下、什么都没有、crash.log 也不存在**（崩溃发生在写日志之前）。

`launch.py` 的修法是把 stream 接到 `runtime.log` 再进 main()。这个脚本钉三件事：

1. 无控制台时进程**能跑到底**（`--selfcheck` 退出码 0）；
2. 那次的输出**落在 runtime.log 里**，不是凭空消失；
3. 有控制台时**行为完全不变**（不建文件、不接管 stream）。

第 3 条是为了防止"兜底把正常路径也改了"——那类改动最难发现，
因为屏幕上看不出区别，直到某个 CI 日志突然变空。
"""

import os
import subprocess
import sys
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "runtime.log"
FAIL = []


def check(name: str, ok: bool, detail: str = "") -> None:
    if ok:
        print(f"[OK] {name}")
    else:
        print(f"[FAIL] {name} {detail}")
        FAIL.append(name)


# 在子进程里把 stream 置空，模拟 PyInstaller --windowed。
# 必须用子进程：在测试进程里置 None 会把自己后续的 print 也炸掉。
_HEADLESS = (
    "import sys, runpy\n"
    "sys.stdout = None\n"
    "sys.stderr = None\n"
    "runpy.run_path('launch.py', run_name='__main__')\n"
)

_CONSOLE = (
    "import sys, runpy\n"
    "runpy.run_path('launch.py', run_name='__main__')\n"
)


def _run(code: str, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["SDL_VIDEODRIVER"] = "dummy"
    env["SDL_AUDIODRIVER"] = "dummy"
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        [sys.executable, "-c", code, *args],
        cwd=str(ROOT), capture_output=True, text=True, timeout=300, env=env,
    )


def _backup() -> bytes | None:
    return LOG.read_bytes() if LOG.exists() else None


def _restore(old: bytes | None) -> None:
    try:
        if old is None:
            LOG.unlink(missing_ok=True)
        else:
            LOG.write_bytes(old)
    except OSError:
        pass


def test_headless_survives() -> None:
    old = _backup()
    try:
        LOG.unlink(missing_ok=True)
        r = _run(_HEADLESS, "--selfcheck")
        check("无控制台时 --selfcheck 退出码 0", r.returncode == 0,
              f"rc={r.returncode} stderr={r.stderr[-400:]}")
        check("无控制台时不往空 stdout 写（stderr 无 NoneType 报错）",
              "NoneType" not in (r.stderr or ""),
              (r.stderr or "")[-300:])
    finally:
        _restore(old)


def test_headless_writes_log() -> None:
    old = _backup()
    try:
        LOG.unlink(missing_ok=True)
        _run(_HEADLESS, "--selfcheck")
        check("无控制台时生成了 runtime.log", LOG.exists(), "（输出无处可去）")
        if not LOG.exists():
            return
        text = LOG.read_text(encoding="utf-8", errors="replace")
        check("runtime.log 里有自检正文", "自检" in text,
              f"实际前 200 字：{text[:200]!r}")
        check("runtime.log 里没有 NoneType 异常", "NoneType" not in text,
              "兜底没生效，print 仍然撞上了空 stream")
    finally:
        _restore(old)


def test_console_path_unchanged() -> None:
    """有控制台时不该建日志文件 —— 兜底不能顺手改掉正常路径。"""
    old = _backup()
    try:
        LOG.unlink(missing_ok=True)
        r = _run(_CONSOLE, "--selfcheck")
        check("有控制台时 --selfcheck 退出码 0", r.returncode == 0,
              f"rc={r.returncode} stderr={r.stderr[-400:]}")
        check("有控制台时输出仍在屏幕上", "自检" in (r.stdout or ""),
              f"stdout 前 200 字：{(r.stdout or '')[:200]!r}")
        check("有控制台时不生成 runtime.log", not LOG.exists(),
              "（正常路径被兜底改动了）")
    finally:
        _restore(old)


def main() -> int:
    test_headless_survives()
    test_headless_writes_log()
    test_console_path_unchanged()
    if FAIL:
        print(f"\nW41 失败 {len(FAIL)} 项：{FAIL}")
        return 1
    print("\nW41 全部通过：没控制台时日志兜得住，有控制台时一切照旧")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
