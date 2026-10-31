"""PyInstaller 单文件打包配置。

目标：给没装 Python 的人一个能双击就跑的 exe。

几个不是默认值的决定，每个都对应一个具体的坑：

1. **不打包 assets/**。游戏零外部素材（精灵、音效都是代码生成的），
   唯一的外部文件是 README 里那几张图，游戏运行时根本读不到它们。
   带进去只会让 exe 大一圈。

2. **`records.json` / `settings.json` 必须写在 exe 旁边，不能写进临时解包目录**。
   onefile 模式会把资源解到 `sys._MEIPASS`，那个目录每次启动都换、
   退出即删 —— 存档写进去等于"打完一局，纪录没了"。
   `records.py` / `settings.py` 用的是 `Path(__file__).parents[2]`，
   打包后 `__file__` 在临时目录里，所以由 `sneak_ai/__main__.py` 里的
   `_frozen_data_dir()` 在启动最早期把路径锚点改到 exe 所在目录。

3. **console**：Windows 上保留控制台窗口。代价是多一个黑框，
   收益是崩溃信息不会凭空消失 —— crash.log 已经兜底，但"看得见的报错"
   永远比"去某个目录找日志"更快。Linux/macOS 下没有这个概念，忽略。

4. **入口是根目录的 `launch.py`，不是 `sneak_ai/__main__.py`**。
   这是第一次真跑打包时踩到的：PyInstaller 把指定文件当**顶层脚本**执行，
   于是 `__main__.py` 的 `__package__` 是空串，`from . import constants`
   直接 `ImportError: attempted relative import with no known parent package`。
   异常发生在导入阶段（连 main() 都没进），crash.log 写不出来，
   用户看到的就是控制台闪一下 —— 而 `python -m sneak_ai` 永远测不出来，
   因为那条路 `__package__` 是正常的。

用法：
    pip install pyinstaller
    python build_exe.py

产物：dist/潜行协议.exe（Windows）/ dist/StealthProtocol（其它平台）
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

NAME = "StealthProtocol" if os.name != "nt" else "潜行协议"


def main() -> int:
    try:
        import PyInstaller.__main__ as pyi
    except ImportError:
        print("缺少 pyinstaller：pip install pyinstaller")
        return 1

    args = [
        # 入口必须是根目录的 launch.py，不能是 sneak_ai/__main__.py：
        # PyInstaller 按顶层脚本执行，后者 `__package__` 为空，
        # `from . import ...` 全部 ImportError（且崩在导入阶段，
        # crash.log 都来不及写，表现就是控制台一闪）。详见 launch.py。
        str(ROOT / "launch.py"),
        "--paths", str(ROOT),
        "--name", NAME,
        "--onefile",
        "--noconfirm",
        "--clean",
        # 隐藏导入：包内部是相对导入，静态分析有时抓不全（尤其是
        # 通过 getattr 分支调用的子模块）。显式声明比事后看报错便宜。
        "--hidden-import", "sneak_ai",
        "--hidden-import", "sneak_ai.core.settings",
        "--hidden-import", "sneak_ai.core.records",
        # 排除掉用不到的重型依赖，能省掉几十 MB
        "--exclude-module", "matplotlib",
        "--exclude-module", "tkinter",
        "--exclude-module", "PyQt5",
        "--exclude-module", "pytest",
    ]
    if os.name == "nt":
        args.append("--console")     # 保留控制台：崩了至少看得见
    pyi.run(args)
    out = ROOT / "dist" / (NAME + (".exe" if os.name == "nt" else ""))
    if out.exists():
        # 别用 emoji：中文 Windows 的 cmd 是 GBK，✅ 会抛 UnicodeEncodeError，
        # 于是"打包成功"的最后一行把自己搞成非零退出 —— 看起来像失败。
        print(f"\n[OK] 已生成 {out}（{out.stat().st_size / 1e6:.1f} MB）")
        print("提示：首次启动会解包，比之后慢 1~2 秒是正常的。")
        print(f"自检：先跑一次 {out.name} --selfcheck，再双击开玩")
        return 0
    print("\n[FAIL] 打包没有产出文件，检查上面的 PyInstaller 输出")
    return 1


if __name__ == "__main__":
    sys.exit(main())
