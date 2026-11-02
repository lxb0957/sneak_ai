"""相位扫描：同一张图，换个哨兵初始相位，机器人还能不能过。

为什么需要：单次 botrun 结果只能代表"这一个相位"。BLOG 经验 31 记过 ——
哨兵一动，全局相位就变，机器人可能撞进一个恰好更宽的窗口，于是
"加了两个人反而快 2 秒"。所以**单次成绩不能拿来判断难不难**，
更不能拿来判断"是不是必死"。

这个脚本给每个哨兵的巡逻相位加一个偏移 k，对 k=0..N-1 各跑一局，
统计胜/败分布。判读标准：

    胜率 > 80%   这一版对机器人来说不难（玩家也偏简单）
    30%~80%      有压力，但有稳定解法 —— 目标区间
    < 30%        可能过不去（或者只有极窄的一条线能过），要回退

跑法：
    SDL_VIDEODRIVER=dummy python scripts/audit_phases.py [--level 2] [--n 10]
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import os
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import pygame  # noqa: E402

from sneak_ai.core.levels import LEVELS  # noqa: E402

import botrun  # noqa: E402


# 摆位逻辑已经不在这里了 —— 见 scripts/botrun.py 的 _place_enemy。
# 历史上这里是个猴补丁（改 Enemy.__init__），只敢插值两点巡逻：多点巡逻
# （第 1 关有 6 个点）相邻两点之间可能隔墙，线性插值会把哨兵放进墙里。
# 结果就是"12 相位"对那批哨兵只有 1 种局面，量出来的胜率虚高。
# 现在两条轴共用同一个落点函数，并且沿"起点 → 落点"逐格验地板。


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--level", type=int, default=-1, help="只测某一关（0 起）")
    ap.add_argument("--n", type=int, default=8, help="相位偏移个数")
    args = ap.parse_args()

    pygame.init()
    targets = range(len(LEVELS)) if args.level < 0 else [args.level]
    for li in targets:
        wins, times, spots = 0, [], []
        nodes_hist, deaths, leg_times = {}, {}, []
        for k in range(args.n):
            # 相位交给 botrun 统一摆位（scripts/botrun.py: _place_enemy）。
            # 早先这里是猴补丁改 Enemy.__init__，只敢插值两点巡逻 ——
            # 于是第 1 关那 6 点巡逻的哨兵在整轮扫描里**一动没动**，
            # 所谓"12 个相位"对这种哨兵只有 1 种局面。现在两条轴共用
            # 同一个落点函数，多点巡逻也沿整条回路均分，分辨率才对得上。
            res = botrun.run(aware=True, immune=False, budget=200.0,
                             weight=7.0, verbose=False, level=li,
                             phase=k / args.n)
            if res["state"] == "victory":
                wins += 1
                times.append(res["time"])
                spots.append(res["spotted"])
            else:
                # 死在哪一格、死前拿了几个节点 —— 这两件事比"胜/负"信息量大得多。
                # 全死在同一个格子 = 关卡只有一个卡口，不是难度合适。
                deaths[res["pos"]] = deaths.get(res["pos"], 0) + 1
            nodes_hist[res["nodes"]] = nodes_hist.get(res["nodes"], 0) + 1
            leg_times.append([lg["t"] for lg in res["legs"]])
        rate = wins / args.n
        avg = sum(times) / len(times) if times else 0.0
        sp = sum(spots) / len(spots) if spots else 0.0
        print(f"[{li + 1}] {LEVELS[li].name}: {wins}/{args.n} 胜（{rate:.0%}）"
              f"   胜局均时 {avg:.1f}s   均被发现 {sp:.1f} 次")
        total_nodes = len(LEVELS[li].terminals)
        hist = " ".join(f"{n}/{total_nodes}:{c}" for n, c in sorted(nodes_hist.items()))
        print(f"    死前拿到几个节点 → {hist}")
        if deaths:
            spot = " ".join(f"{p}×{c}" for p, c in sorted(deaths.items(),
                                                          key=lambda kv: -kv[1]))
            print(f"    阵亡点 → {spot}")
        if total_nodes > 1:
            # 每一段的中位到达时刻：哪一段最费时间，一眼看得出来
            for j in range(total_nodes):
                vals = sorted(row[j] for row in leg_times if len(row) > j)
                if vals:
                    print(f"    第 {j + 1} 个节点：{len(vals)}/{args.n} 局拿到，"
                          f"中位 {vals[len(vals) // 2]:.1f}s")
    pygame.quit()


if __name__ == "__main__":
    main()
