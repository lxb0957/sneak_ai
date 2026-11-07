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

## 判读：别看"两个区间叠不叠"，看差值自己的区间

这个脚本以前会打一行 `区间 [lo1-hi2, hi1-lo2]`，并且在文档里写
"两个区间重叠 = 分不出高低"。**那是错的**，而且错得很常见：

- 两个 95% 边际区间各取最坏情况拼出来的那个区间，实际只覆盖 ~90%，
  拿它当 95% 用，本来就偏保守；
- 更关键的是**方向反了**：区间重叠推不出"差异不显著"。两个边际区间
  重叠 4 个百分点，差值区间完全可能整段在零以上。

正确做法是直接对**差值**做区间（Newcombe 混合得分法）并给出检验 p 值。
本脚本现在两者都打；关卡是按 seed 配对的，所以同时给 McNemar 精确检验
（只看"翻盘局"），它比独立样本的检验更有力。
"""
import argparse
import math
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


def difference_interval(k1, n1, k2, n2, z=1.96):
    """两个比例之差的 Newcombe 区间（不配对时用）。

    公式：d ± sqrt((p1-lo1)² + (hi2-p2)²) / sqrt((hi1-p1)² + (p2-lo2)²)
    —— 把两侧边际区间的"半个宽度"按平方和合起来。

    为什么不直接 `[lo1-hi2, hi1-lo2]`：那个区间是两个 95% 各取最坏情况
    拼出来的，实际只有约 90% 覆盖；而且它宽得离谱，会让 16 个点的真实
    差异看起来"跨零"。Newcombe 是被验证过在 95% 上表现良好的近似。
    """
    p1, p2 = k1 / n1, k2 / n2
    lo1, hi1 = wilson(k1, n1, z)
    lo2, hi2 = wilson(k2, n2, z)
    d = p1 - p2
    low = d - math.sqrt((p1 - lo1) ** 2 + (hi2 - p2) ** 2)
    high = d + math.sqrt((hi1 - p1) ** 2 + (p2 - lo2) ** 2)
    return d, low, high


def mcnemar_exact(b, c):
    """配对样本的精确 McNemar：只看"只有一边赢"的局（b、c）。

    b = 只有 A 赢；c = 只有 B 赢。零假设下 b ~ Bin(b+c, 0.5)。
    """
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / (2 ** n)
    return min(1.0, 2 * tail)


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
        flags = []          # 逐局的胜负，配对检验要按 i 对齐
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
            flags.append(r["state"] == C.STATE_VICTORY)
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
        rows[li] = (wins, lo, hi, flags)
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
            n1, n2 = rows[a1][0], rows[a2][0]
            d, lo, hi = difference_interval(n1, a.runs, n2, a.runs)
            # 配对：第 i 局在两关用的是同一颗种子，所以能逐局比"翻盘"
            f1, f2 = rows[a1][3], rows[a2][3]
            only1 = sum(1 for x, y in zip(f1, f2) if x and not y)
            only2 = sum(1 for x, y in zip(f1, f2) if y and not x)
            p = mcnemar_exact(only1, only2)
            verdict = "显著" if p < 0.05 else "不显著"
            print(f"Δ L{a1 + 1}→L{a2 + 1} = {d:+.1%}  "
                  f"（差值 95%CI [{lo:+.0%}, {hi:+.0%}]，"
                  f"配对翻盘 {only1}:{only2}，McNemar p={p:.3f} → {verdict}）")
            # 提醒一句老坑：边际区间重叠不代表差值为零
            if lo <= 0.0 <= hi:
                print("  注意：差值区间跨零 —— 这才叫「分不出高低」；"
                      "两个边际区间重叠本身不构成这个结论。")


if __name__ == "__main__":
    main()
