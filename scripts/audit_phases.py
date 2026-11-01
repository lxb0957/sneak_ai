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
from sneak_ai.entity import enemy as enemy_mod  # noqa: E402

import botrun  # noqa: E402


_ORIG_INIT = enemy_mod.Enemy.__init__
_PHASE_OFFSET = [0]


def _patched_init(self, x, y, patrol=None, perception=None, chase_speed=None):
    _ORIG_INIT(self, x, y, patrol=patrol, perception=perception,
               chase_speed=chase_speed)
    if self.patrol:
        self._patrol_idx = (self._patrol_idx + _PHASE_OFFSET[0]) % len(self.patrol)


enemy_mod.Enemy.__init__ = _patched_init


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--level", type=int, default=-1, help="只测某一关（0 起）")
    ap.add_argument("--n", type=int, default=8, help="相位偏移个数")
    args = ap.parse_args()

    pygame.init()
    targets = range(len(LEVELS)) if args.level < 0 else [args.level]
    for li in targets:
        wins, times, spots = 0, [], []
        for k in range(args.n):
            _PHASE_OFFSET[0] = k
            res = botrun.run(aware=True, immune=False, budget=200.0,
                             weight=7.0, verbose=False, level=li)
            if res["state"] == "victory":
                wins += 1
                times.append(res["time"])
                spots.append(res["spotted"])
        rate = wins / args.n
        avg = sum(times) / len(times) if times else 0.0
        sp = sum(spots) / len(spots) if spots else 0.0
        print(f"[{li + 1}] {LEVELS[li].name}: {wins}/{args.n} 胜（{rate:.0%}）"
              f"   胜局均时 {avg:.1f}s   均被发现 {sp:.1f} 次")
    pygame.quit()


if __name__ == "__main__":
    main()
