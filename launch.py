"""打包入口 —— 只做一件事：把包内的 main() 叫起来。

为什么不能直接拿 `sneak_ai/__main__.py` 当打包入口：
PyInstaller 是**按脚本**执行的，`python sneak_ai/__main__.py` 和
`python -m sneak_ai` 在语义上并不一样。前者把文件当成顶层脚本，
`__package__` 是空字符串，于是 `__main__.py` 里那一堆
`from . import constants as C` 全部炸掉：

    ImportError: attempted relative import with no known parent package

这个异常发生在**导入阶段**，还没进 main()，所以连 crash.log 都写不出来 ——
双击 exe 看到的就是控制台闪一下。用 `python -m sneak_ai` 测一百次都测不出来，
因为那条路 `__package__ == "sneak_ai"`。

根目录放一个 launcher 是最省事的解法：它是顶层脚本，用**绝对导入**
把包拉进来，包的相对导入随之恢复正常。`python -m sneak_ai` 那条路
仍然由 `__main__.py` 里的 `if __name__ == "__main__"` 兜着，两边都能跑。
"""

import sys

from sneak_ai.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
