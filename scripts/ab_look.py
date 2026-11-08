"""尺子的两个旋钮：往前看多远、多早停下来等。

为什么值得单独测：`diag_level4.py` 把第 4 关的败局切开以后，露出来的是
**机器人中位 7.1 秒就被看见**，然后整局 60%~70% 的时间都在 FLEE。
也就是说 25% 这个数字量的不是"走廊有多窄"，是"警报响了以后能撑多久"。
如果这是真的，那么动地图必然没用（九次实验已经证明没用）——
唯一还有意义的改动是让它在**被看见之前**就停下来。

可"多等一会儿"在这个项目里是反直觉的：九次实验的规律是"凡是让机器人
多待一秒的改动都更差"。所以不能再靠推理，只能配对测。

    look=6   往前看 6 格（1.4 秒 × 走路 4.4 格/秒）—— 现行基线
    look=9   3 秒
    look=12  4.5 秒，约等于一个视锥横扫周期

跑法：
    SDL_VIDEODRIVER=dummy python scripts/ab_look.py --level 3 --runs 96
"""
import argparse
import math
import os
import random
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame  # noqa: E402
pygame.init()

import botrun  # noqa: E402
from sneak_ai import constants as C  # noqa: E402
from sneak_ai.core.levels import LEVELS  # noqa: E402


def mcnemar_exact(b, c):
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    return min(1.0, 2 * sum(math.comb(n, i) for i in range(k + 1)) / (2 ** n))


def play(level, seed, jitter, loot, look, hold):
    rng = random.Random(seed)
    delay = rng.uniform(0.0, jitter)
    seen = [None]

    def on_frame(t, g, mode):
        if seen[0] is None and g.world.spotted > 0:
            seen[0] = round(t, 1)

    r = botrun.run(level=level, rng=rng, start_delay=delay, loot=loot,
                   look=look, hold=hold, on_frame=on_frame)
    return {"win": r["state"] == C.STATE_VICTORY, "time": r["time"],
            "seen": seen[0], "pos": r["pos"]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--level", type=int, default=3)
    ap.add_argument("--runs", type=int, default=96)
    ap.add_argument("--seed", type=int, default=31337)
    ap.add_argument("--jitter", type=float, default=1.0)
    ap.add_argument("--cands", default="9:0.25,12:0.25,12:0.15",
                    help="候选 look:hold，逗号分隔；基线恒为 6:0.25")
    a = ap.parse_args()

    lv = LEVELS[a.level]
    loot = lv.resource_critical
    seeds = [a.seed + i for i in range(a.runs)]
    print(f"=== 尺子旋钮 A/B：L{a.level + 1} {lv.name}，{a.runs} 局配对，"
          f"seed={a.seed}，jitter={a.jitter} ===")

    base = {}
    for sd in seeds:
        base[sd] = play(a.level, sd, a.jitter, loot, 6, 0.25)
    bw = sum(v["win"] for v in base.values())
    print(f"  基线 look=6 hold=0.25   {bw:>3}/{a.runs} = {bw / a.runs:5.1%}"
          f"   首次被发现中位 {_med([v['seen'] for v in base.values()])}")

    for spec in a.cands.split(","):
        look_s, hold_s = spec.split(":")
        look, hold = int(look_s), float(hold_s)
        rows = {sd: play(a.level, sd, a.jitter, loot, look, hold) for sd in seeds}
        w = sum(v["win"] for v in rows.values())
        only_b = sum(1 for sd in seeds if base[sd]["win"] and not rows[sd]["win"])
        only_c = sum(1 for sd in seeds if rows[sd]["win"] and not base[sd]["win"])
        p = mcnemar_exact(only_b, only_c)
        print(f"  look={look:<3d} hold={hold:<5.2f}   {w:>3}/{a.runs} = {w / a.runs:5.1%}"
              f"   首次被发现中位 {_med([v['seen'] for v in rows.values()])}"
              f"   翻盘 基线:候选 {only_b}:{only_c}  p={p:.3f}")
        pairs = [(base[sd]["time"], rows[sd]["time"]) for sd in seeds
                 if base[sd]["win"] and rows[sd]["win"]]
        if pairs:
            d = sum(o - n for o, n in pairs) / len(pairs)
            print(f"  {'':<18}  都通过的 {len(pairs)} 局：用时差（基线-候选）"
                  f"{d:+.1f}s（正 = 候选更快）")
        # 只在"胜率没变"时才有意义：改的是不是机制
        if w == bw:
            print(f"  {'':<18}  胜率一模一样 —— 这个旋钮对本关没有作用，别调了。")


def _med(xs):
    xs = [x for x in xs if x is not None]
    return f"{statistics.median(xs):.1f}s" if xs else "——"


if __name__ == "__main__":
    main()
