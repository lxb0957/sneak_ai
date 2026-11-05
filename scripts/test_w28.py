"""W28：控制台编码 —— 诊断输出不许把诊断本身搞崩。

## 这条测试是 CI 抓出来的，不是想出来的

v1.8.7 第一次跑发布流水线，`build (win64)` job 在 Windows runner 上：

    打包 → 成功
    自检 → UnicodeEncodeError: 'charmap' codec can't encode characters in position 1-2
           退出码 1 → 流水线红

本地怎么跑都是绿的。原因很朴素：**这台机器是中文 Windows（cp936），
中文写得出去；GitHub 的 runner 是英文 Windows（cp1252），中文写不出去。**
一个只在这台机器上成立的"通过"。

这跟 BLOG 第 23 章是同一个坑的另一种形态：当年是 emoji 撞上 cp936 的 cmd，
当时的修法是把 ✅ 换成 [OK] —— **只改台词，没改机制**，
所以它换了个方向回来：这次是中文撞上 cp1252。

所以这个文件钉的不是"某行 print 用了什么字符"，是**机制**：
不管 stdout 是什么编码，写一句中文日志都不该让进程以非零码退出。

## 三条断言

1. `_harden_console()` 能把一个 cp1252 的流变成"写得进中文"
2. 真实入口 `launch.py --selfcheck` 在 `PYTHONIOENCODING=cp1252` 下退出码 0
3. 而且输出里**真的有中文** —— 否则"不崩"可以靠把内容全丢掉来达成，
   那等于把诊断功能删了换一个绿勾
"""

from __future__ import annotations

import io
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")


def test_harden_console_makes_cp1252_writable():
    """把 stdout 换成 cp1252 的流，加固之后中文必须写得进去。"""
    from sneak_ai.__main__ import _harden_console

    raw = io.BytesIO()
    stream = io.TextIOWrapper(raw, encoding="cp1252", errors="strict")
    # 先确认这个流真的会炸：不然这条测试就是空的
    try:
        stream.write("自检")
        stream.flush()
        raise AssertionError("cp1252 居然能写中文？这个测试的前提没了")
    except UnicodeEncodeError:
        pass

    saved = sys.stdout
    try:
        sys.stdout = stream
        _harden_console()
        stream.write("自检通过\n")
        stream.flush()
        out = raw.getvalue()
    finally:
        sys.stdout = saved

    assert "自检通过".encode("utf-8") in out, f"没有按 UTF-8 写出去：{out!r}"
    print("[OK] cp1252 的流被加固成 UTF-8，中文能写出去")


def _run(args, extra_env=None):
    env = dict(os.environ)
    env.update({"SDL_VIDEODRIVER": "dummy", "SDL_AUDIODRIVER": "dummy"})
    env.pop("PYTHONIOENCODING", None)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, str(ROOT / "launch.py"), *args],
        cwd=ROOT, env=env, capture_output=True, timeout=300,
    )


def test_selfcheck_survives_cp1252():
    """真实入口 + 英文 Windows 的编码：退出码必须是 0，不能是"日志炸了"。"""
    r = _run(["--selfcheck"], {"PYTHONIOENCODING": "cp1252"})
    err = r.stderr.decode("utf-8", "replace")
    assert r.returncode == 0, f"cp1252 下自检失败（rc={r.returncode}）：\n{err[-1500:]}"
    print(f"[OK] PYTHONIOENCODING=cp1252 下 --selfcheck 退出码 0（{len(r.stdout)} 字节输出）")


def test_selfcheck_output_keeps_chinese():
    """不崩还不够：内容不能靠"丢弃"换绿。"""
    r = _run(["--selfcheck"], {"PYTHONIOENCODING": "cp1252"})
    out = r.stdout.decode("utf-8", "replace")
    assert "自检" in out, f"输出里没有中文了，说明是靠丢内容过的关：{out[:300]!r}"
    assert "[OK]" in out, f"没有成功标记：{out[-300:]!r}"
    print("[OK] 输出仍是中文且含 [OK] 标记（不是靠丢内容换的绿）")


def test_bad_argument_survives_cp1252():
    """报错路径也得活着：`--level 99` 的中文提示走的是 stderr。"""
    r = _run(["--level", "99"], {"PYTHONIOENCODING": "cp1252"})
    assert r.returncode == 2, f"参数错误应该返回 2，实际 {r.returncode}"
    err = r.stderr.decode("utf-8", "replace")
    assert "只能是" in err, f"stderr 里没有那句中文提示：{err[:300]!r}"
    print("[OK] 参数错误的中文提示在 cp1252 下也写得出，退出码 2")


def main():
    print("=== W28：控制台编码（诊断输出不许把诊断搞崩）===")
    test_harden_console_makes_cp1252_writable()
    test_selfcheck_survives_cp1252()
    test_selfcheck_output_keeps_chinese()
    test_bad_argument_survives_cp1252()
    print("\nW28 全部通过：换一个代码页，自检照样能说出它在干什么。")


if __name__ == "__main__":
    main()
