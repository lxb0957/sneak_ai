"""关卡难度的**可信**测量（带置信区间 + 跨关配对 + 死点分布）。

为什么不能直接用 `botrun --runs N`：

1. **不给区间**。判断"第 2 关和第 3 关谁更难"时点估计没用 —— n=24 的 95%
   区间宽达 ±20 个百分点，两次采样能差 17 个点。这个项目已经因为这件事反复
   误判：v1.8c 写过"中间两关的梯度是尺子给的"，v1.8d 又在同一段平坦区间上
   读出"L3 明显更难"。两次都不是关卡变了，是样本太小。
2. **不做配对**。第 i 局在每关用同一个种子，抵消"开局相位运气"这个方差大头；
   梯度看配对差，不看两个点估计相减。
3. **不给死点分布**。关卡缺陷（卡口）只体现在"某几个格子吃掉三成败局"上，
   胜率本身看不出来。

三条一起看才回答得了"这条曲线可信吗"，缺一条就会得到"看起来像结论的噪声"。

跑法：
    python scripts/measure_levels.py --levels 0,1,2,3 --runs 96 --seed 31337

经验值（这台机器）：单进程约 55 局/分钟；五关并行各 96 局约 9 分钟。
判读：两个区间重叠 = 分不出高低，别下结论。
"""
import argparse
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import os
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import pygame  # noqa: E402
pygame.init()

import botrun  # noqa: E402
from sneak_ai import constants as C  # noqa: E402
from sneak_ai.core.levels import LEVELS  # noqa: E402


def wilson(k, n, z=1.96):
    """二项比例的 Wilson 区间。

    为什么不用 `p ± 1.96·sqrt(p(1-p)/n)`：p=0 或 p=1 时那个公式宽度是 0，
    等于宣称"10 局全胜 = 必胜"。Wilson 在边界上仍有宽度（0/10 的上界约 28%），
    这才是"小样本说不出狠话"该有的样子。
    端点夹到 [0,1]：p=0 时下界会算出 -1e-17 这种浮点毛刺。
    """
    if n == 0:
        return 0.0, 0.0
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    m = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5)
    return max(0.0, (c - m) / d), min(1.0, (c + m) / d)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--levels", default="1,2")
    ap.add_argument("--runs", type=int, default=64)
    ap.add_argument("--seed", type=int, default=777)
    ap.add_argument("--jitter", type=float, default=1.0)
    ap.add_argument("--tag", default="")
    # 补给开关：auto = 按关卡数据（第 5 关开、前四关关）。
    # 为什么要能强制 off：第 5 关是唯一开 loot 的一关，那个数字和前四关
    # 的口径不一样。要回答"五关摆在一起，谁更难"就得用同一个口径再量一遍。
    ap.add_argument("--loot", choices=("auto", "on", "off"), default="auto")
    a = ap.parse_args()

    levels = [int(x) for x in a.levels.split(",")]
    # 配对：同一个 i 在所有关卡共用 seed+i。注意不能用同一个 Random 对象，
    # 各关抽到的相位序列会互相污染，就不再是"配对"了。
    rows = {}
    for li in levels:
        wins, times = 0, []
        # 战死和超时必须分开记。混在一起会骗人：第 5 关第一版 96 局里
        # 38 局"败局"其实是**卡在第二道闸前站满 200 秒**（一次没死），
        # 而工具把它们当死点打出来，指向的是一格根本没杀过人的空地。
        # 一格吃掉多少败局 → 死点；一格吃掉多少次超时 → 卡点。
        # 两者的修法完全不同（一个是关卡卡口，一个是尺子不会走）。
        deaths, stalls = {}, {}
        for i in range(a.runs):
            rng = random.Random(a.seed + i) if a.jitter > 0 else None
            delay = rng.uniform(0.0, a.jitter) if rng else 0.0
            # 补给是不是通关必需，写在这一关的数据上（Level.resource_critical）。
            # 第 5 关五颗石子换五道闸：不开 loot 量到的是"没带够弹药有多难"，
            # 不是"这关多难"。前四关仍按老规矩（不捡），历史数字才可比。
            loot = (LEVELS[li].resource_critical if a.loot == "auto"
                    else a.loot == "on")
            r = botrun.run(level=li, rng=rng, start_delay=delay, loot=loot)
            if r["state"] == C.STATE_VICTORY:
                wins += 1
                times.append(r["time"])
            elif r["state"] == C.STATE_DEFEAT:
                key = r["pos"]
                deaths[key] = deaths.get(key, 0) + 1
            else:
                key = r["pos"]
                stalls[key] = stalls.get(key, 0) + 1
        lo, hi = wilson(wins, a.runs)
        avg_t = sum(times) / len(times) if times else 0.0
        rows[li] = (wins, lo, hi)
        losses = a.runs - wins
        n_dead = sum(deaths.values())
        hot = sorted(deaths.items(), key=lambda kv: -kv[1])[:3]
        hot_s = "  ".join(
            f"{k}x{v}={v / n_dead:.0%}" if n_dead else f"{k}x{v}"
            for k, v in hot
        ) or "-"
        stall_n = sum(stalls.values())
        print(f"L{li + 1} {LEVELS[li].name:<6} {wins:>3}/{a.runs} = "
              f"{wins / a.runs:5.1%}  95%CI[{lo:.0%},{hi:.0%}]  "
              f"胜局均时 {avg_t:5.1f}s  战死 {losses - stall_n} / 卡死 {stall_n}"
              f"  热死点 {hot_s}")
        if stall_n:
            hot_stall = sorted(stalls.items(), key=lambda kv: -kv[1])[:2]
            print("  卡点 " + "  ".join(f"{k}x{v}" for k, v in hot_stall))
        # 死点比胜率更能指出"错在哪"：一格吃掉 8% 以上败局，基本就是一道
        # 卡在必经路上的门（v1.8.2 第 2 关 (33,5) 占 16%，v1.8d 第 3 关 (15,55) 占 17%）。
        # 只提示、不判失败：阈值是经验值，具体那一格是不是设计意图要人看。
        # 加 n >= 3 的下限：n=16 局时一格死 1 次就是 17%，光看占比会把小样本的
        # 噪声全报成"死点集中"（第一次加这条时 6 个败局刷了 6 行告警）。
        for pos, n in hot:
            if n >= 3 and n_dead and n / n_dead >= 0.08:
                print(f"  ! 死点集中：{pos} 一格吃掉 {n / n_dead:.0%} 的战死"
                      f" —— 先查它是不是必经路上那道会转身的门")

    if len(levels) >= 2:
        for i in range(len(levels) - 1):
            a1, a2 = levels[i], levels[i + 1]
            d = (rows[a1][0] - rows[a2][0]) / a.runs
            print(f"Δ L{a1 + 1}→L{a2 + 1} = {d:+.1%}  "
                  f"（区间 [{rows[a1][1] - rows[a2][2]:+.0%}, {rows[a1][2] - rows[a2][1]:+.0%}]）")


if __name__ == "__main__":
    main()
