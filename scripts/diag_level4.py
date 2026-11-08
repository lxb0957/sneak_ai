"""第 4 关的死因切片：不猜，把每一局拆成时间线看。

为什么还需要一个脚本：`measure_levels.py` 只回答"多少局赢、死在哪一格"。
第 4 关的死点是一整段走廊（不是一格门），所以那个输出告诉不了你
"死的时候黑灯还剩几秒"。而这一关的全部难度都压在那 10 秒窗口上 ——
拉闸早了两步、晚了一步，结果完全一样（死于 x=58），原因却完全相反。

这个脚本按帧采样四件事，然后按"黑灯还开着没有"把败局切开：

  1. **黑灯窗口用在哪了**：拉闸时刻 → 数据舱到达时刻 → 死亡时刻
  2. **死的时候黑灯剩几秒**（0 = 窗口已经过期）
  3. **从第一次被看见到被抓隔了多久**（回答"能不能跑掉"）
  4. **每个模式待了多久**（HOLD 多 = 在等窗口；FLEE 多 = 已经暴露）

用法：
    SDL_VIDEODRIVER=dummy python scripts/diag_level4.py --runs 96
    SDL_VIDEODRIVER=dummy python scripts/diag_level4.py --runs 96 --hot 3
"""
import argparse
import collections
import os
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import pygame  # noqa: E402
pygame.init()

import botrun  # noqa: E402
from sneak_ai import constants as C  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=96)
    ap.add_argument("--seed", type=int, default=31337)
    ap.add_argument("--jitter", type=float, default=1.0)
    ap.add_argument("--level", type=int, default=3, help="第 4 关 = 索引 3")
    ap.add_argument("--hot", type=int, default=0,
                    help="打印最慢若干局的末日时间线（排查用）")
    a = ap.parse_args()

    wins = 0
    modes = collections.Counter()          # 每个模式累计秒数（只统计败局/全部）
    modes_win = collections.Counter()
    deaths = []                            # 每局一条 dict
    pull_t, node_t, first_seen_t = [], [], []
    timeline = collections.Counter()       # 每 10 秒还活着的局数
    timelines = {}                         # 只给 --hot 用

    for i in range(a.runs):
        rng = random.Random(a.seed + i) if a.jitter > 0 else None
        delay = rng.uniform(0.0, a.jitter) if rng else 0.0

        st = {"pull": None, "node": None, "seen": None, "spotted": 0,
              "modes": collections.Counter(), "frames": []}

        def on_frame(t, g, mode):
            w, p = g.world, g.player
            # 模式计时按帧累加（1/60 秒一帧，误差 < 2%）
            st["modes"][mode] += 1 / 60
            # 拉闸：blackout_left 刚变正的那一帧
            if st["pull"] is None and w.blackout_left > 0.0:
                st["pull"] = round(t, 1)
            if st["node"] is None and w.taken:
                st["node"] = round(t, 1)
            if st["seen"] is None and w.spotted > 0:
                st["seen"] = round(t, 1)
            st["spotted"] = w.spotted
            # 每 6 帧留一格（10 样本/秒）：200 秒也只有 2000 条，
            # 既能看开局怎么暴露的，也能看死前发生了什么。
            if int(t * 60) % 6 == 0:
                st["frames"].append((round(t, 1), mode, round(p.x, 1), round(p.y, 1),
                                     round(w.blackout_left, 1), w.spotted))

        r = botrun.run(level=a.level, rng=rng, start_delay=delay, on_frame=on_frame)
        win = r["state"] == C.STATE_VICTORY
        wins += win
        if win:
            modes_win.update(st["modes"])
        else:
            modes.update(st["modes"])
        if st["pull"] is not None:
            pull_t.append(st["pull"])
        if st["node"] is not None:
            node_t.append(st["node"])
        if st["seen"] is not None:
            first_seen_t.append(st["seen"])
        # 存活曲线：每 10 秒记一次"这一局到这时还没结束"
        end = r["time"]
        for s in range(0, int(end) + 1, 10):
            if s <= end:
                timeline[s] += 1
        if not win:
            # 死的时候黑灯还剩几秒：从帧里取最后一帧的黑灯值
            bl = st["frames"][-1][4] if st["frames"] else 0.0
            deaths.append({
                "seed": a.seed + i, "state": r["state"], "t": r["time"],
                "pos": r["pos"], "blackout_left": bl,
                "pull": st["pull"], "node": st["node"], "seen": st["seen"],
                "spotted": r["spotted"],
                "seen_to_death": (round(r["time"] - st["seen"], 1)
                                  if st["seen"] is not None else None),
            })
            if a.hot and len(timelines) < a.hot:
                timelines[a.seed + i] = st["frames"]

    n = a.runs
    print(f"\n第 {a.level + 1} 关  {wins}/{n} = {wins / n:.1%}"
          f"  （seed {a.seed}..{a.seed + n - 1}, jitter {a.jitter}）")

    # ---- 1. 黑灯窗口的时间线 ----
    def stat(name, xs):
        if not xs:
            print(f"  {name}: ——")
            return
        xs = sorted(xs)
        print(f"  {name}: 中位 {xs[len(xs) // 2]:.1f}s"
              f"  25% {xs[len(xs) // 4]:.1f}s  75% {xs[3 * len(xs) // 4]:.1f}s"
              f"  n={len(xs)}")

    print("\n[1] 拉闸 / 摸数据 / 第一次被看见")
    stat("拉闸时刻", pull_t)
    stat("摸到数据时刻", node_t)
    stat("第一次被看见", first_seen_t)

    # ---- 2. 死的时候黑灯还在不在 ----
    dead = [d for d in deaths if d["state"] == C.STATE_DEFEAT]
    stall = [d for d in deaths if d["state"] != C.STATE_DEFEAT]
    print(f"\n[2] 败局 {len(deaths)}（战死 {len(dead)} / 卡死 {len(stall)}）")
    if dead:
        in_bl = [d for d in dead if d["blackout_left"] > 0.2]
        print(f"  死时黑灯还开着: {len(in_bl)}/{len(dead)} = {len(in_bl) / len(dead):.0%}"
              f"（黑灯只覆盖 10 秒 / 约 44 格，覆盖不到整条 74 格直廊）")
        # 按"拉闸后多少秒死"分桶：窗口 10 秒，看死亡是不是堆在窗口关闭之后
        buckets = collections.Counter()
        for d in dead:
            if d["pull"] is None:
                buckets["没拉闸"] += 1
            else:
                gap = d["t"] - d["pull"]
                buckets[f"{int(gap // 10) * 10:>2d}~{int(gap // 10) * 10 + 10:>2d}s"] += 1
        print("  拉闸 → 死亡 的时间差分布（窗口 = 10s）")
        for k in sorted(buckets):
            bar = "█" * max(1, round(buckets[k] / max(1, len(dead)) * 40))
            print(f"    {k:>8s}  {buckets[k]:>3d}  {bar}")

    # ---- 3. 被发现到被抓：能不能跑掉 ----
    if dead:
        gaps = [d["seen_to_death"] for d in dead if d["seen_to_death"] is not None]
        if gaps:
            gaps.sort()
            print(f"\n[3] 第一次被看见 → 被抓：中位 {gaps[len(gaps) // 2]:.1f}s"
                  f"  n={len(gaps)}"
                  f"\n    数学：满体力冲刺 100/34=2.94s × 相对速度 2.0 = 5.9 格"
                  f" < 视野 6.5 格 → 看住之后拉不出视野")

    # ---- 4. 模式占用 ----
    print("\n[4] 模式时间占比（败局 / 胜局）")
    tot_l = sum(modes.values()) or 1
    tot_w = sum(modes_win.values()) or 1
    for m in sorted(set(modes) | set(modes_win)):
        print(f"    {m:<5s} 败 {modes[m] / tot_l:5.1%}  "
              f"({modes[m]:6.0f}s)   胜 {modes_win[m] / tot_w:5.1%}"
              f"  ({modes_win[m]:6.0f}s)")

    # ---- 5. 存活曲线 ----
    print("\n[5] 存活曲线（每 10 秒还剩几局没结束）")
    for s in sorted(timeline):
        left = timeline[s]
        print(f"    {s:>3d}s  {left:>3d}/{n}  " + "█" * max(0, round(left / n * 40)))

    # ---- 6. 死点分布（按 x 分段：这关是东西向直廊，x 就是进度）----
    if dead:
        print("\n[6] 战死的 x 分布（x=5 出生，x=88 数据舱）")
        bands = collections.Counter()
        for d in dead:
            x = d["pos"][0]
            bands[(x // 10) * 10] += 1
        for k in sorted(bands):
            print(f"    x {k:>2d}~{k + 9:>2d}  {bands[k]:>3d}  "
                  + "█" * max(1, round(bands[k] / len(dead) * 40)))

    if a.hot:
        print("\n[7] 末日时间线（最慢的几局，每 6 帧取 1 帧 = 每秒 1 行）")
        for seed, frames in timelines.items():
            print(f"\n  seed {seed}")
            for f in frames:
                print(f"    t={f[0]:5.1f} [{f[1]:4s}] ({f[2]:5.1f},{f[3]:4.1f})"
                      f" 黑灯 {f[4]:4.1f}s 被发现 {f[5]}")


if __name__ == "__main__":
    main()
