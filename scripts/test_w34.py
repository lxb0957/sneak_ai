"""W34：尺子上的两个旋钮 —— 默认值必须一格不动，旋钮必须真的接着线。

## 为什么需要这个

第 4 关卡在 25%，九次改地图全部更差。于是这一轮把问题倒过来问：
**是不是尺子（`botrun`）量错了？**

`diag_level4.py` 把败局按时间切开以后，答案很难看：96 局里
**一次都没被发现的局是 0 局**，第一次被看见的中位时间是 7.3 秒，
而整局 60%~70% 的时间都耗在 FLEE 上。也就是说 25% 量的不是"走廊有多窄"，
是"警报响了之后能撑多久"。

于是给机器人加了两个本该一开始就在的旋钮：

    look  往前看几格算"前方被照着"（默认 6 格 ≈ 1.4 秒）
    hold  危险度超过多少就停下来等（默认 0.25）

配对实测（`ab_look.py`，96 局同种子）把 look 拉到 12 格：
首次被发现从 7.3 秒推到 10.0 秒，胜率 25.0% → 27.1%，**p=0.86**。
延迟被发现这件事真的做到了，胜率一点没动。这条否证比任何解释都有用：
"被看见得太早"不是那 75% 的来源。

## 这组测试守两件事

1. **默认参数 = 历史基线**。look=6 / hold=0.25 必须和加参数之前逐字段相同
   （状态、用时、落点、被发现次数、拿到的节点数，五个字段全对上，见 `PINNED`）——
   否则 README 里前四关的所有历史数字就都换了个尺子，而没有任何东西会报错。
2. **旋钮真的接着线**。换个值必须换出不同的结局，不然它只是个装饰用的参数，
   而"扫了一遍没变化"会被读成"这个方向没用"。

用法：
    python scripts/test_w34.py
"""

from __future__ import annotations

import os
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame  # noqa: E402

pygame.init()

import ab_look  # noqa: E402
import botrun  # noqa: E402

# 锚点：第 4 关、seed 31337、jitter 1.0 的第一局。
# 这不是"随便记一个数"，它是"尺子没变"的锚点 —— 地图一动它就该变，
# 参数一动它不该变。
#
# v1.8.11 重锚了一次：那版把视线几何从"0.5 格采样"换成精确网格步进
# （`grid.dda_hit_t`），旧实现会整格跳过墙（8 格内 1.3% 的视线"隔墙看见"），
# 所以**感知规则本身变了**，同一局自然走不出同一个结局。
# 这不是"参数漂了"，是"尺子的刻度换了" —— 换刻度就要重新读一遍所有历史数字：
# README 的难度表、KNOWN_ISSUES 里第 4 关的 25%，都是在这条线之后重测的。
PINNED = ("victory", 50.3, (6, 26), 4, 1)


def _once(**kw):
    """跑一局固定种子。注意种子对象每次都要新建：
    复用同一个 Random 会让第二次从"已经抽过"的状态继续抽，配不上对。"""
    rng = random.Random(31337)
    delay = rng.uniform(0.0, 1.0)
    r = botrun.run(level=3, rng=rng, start_delay=delay, **kw)
    return (r["state"], r["time"], r["pos"], r["spotted"], r["nodes"])


def test_defaults_are_the_pinned_baseline():
    assert _once() == PINNED, f"默认参数变了：{_once()} != {PINNED}"
    # 显式写出默认值也必须一样 —— 证明"省略"和"写上"走的是同一条路径
    assert _once(look=6, hold=0.25) == PINNED, "显式默认值和省略不一致"
    print(f"[OK] 默认 (look=6, hold=0.25) 与加参数之前的基线逐字段相同：{PINNED}")


def test_knobs_are_wired():
    a = _once()
    b = _once(look=12, hold=0.15)
    assert a != b, "换了 look/hold 结果没变 —— 旋钮没接上线（扫表会读出假结论）"
    print(f"[OK] look=12 / hold=0.15 换出了不同的结局：{b}")


def test_mcnemar_sanity():
    # 一局都没翻 → 说"分不出"而不是"没变化"
    assert ab_look.mcnemar_exact(0, 0) == 1.0
    assert ab_look.mcnemar_exact(5, 5) == 1.0
    # 对称：谁在前谁在后不该影响 p
    assert abs(ab_look.mcnemar_exact(3, 9) - ab_look.mcnemar_exact(9, 3)) < 1e-12
    # 一边倒的 0:10 是显著的，不能因为样本小就报 1.0
    assert ab_look.mcnemar_exact(0, 10) < 0.01, ab_look.mcnemar_exact(0, 10)
    print("[OK] McNemar 精确检验：一边倒显著、对称、零翻盘 = 1.0")


def test_docs_list_the_new_rulers():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for name in ("diag_level4.py", "ab_look.py"):
        assert name in readme, f"README 没提 {name} —— 尺子的改动必须写在能看见的地方"
    print("[OK] README 的测试清单里有两个新脚本（改尺子要留痕）")


def main() -> int:
    test_defaults_are_the_pinned_baseline()
    test_knobs_are_wired()
    test_mcnemar_sanity()
    test_docs_list_the_new_rulers()
    print("\nW34 全部通过：默认值一格没动，旋钮确实接着线")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
