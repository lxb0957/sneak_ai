"""同种子配对 A/B：这条新逻辑到底翻了几局？

为什么不能靠"两次独立采样 + 两个 Wilson 区间"回答这个问题：

    改前 75.0%（区间 65~83）
    改后 67.7%（区间 58~76）

区间重叠，于是只能说"分不出来"。但这两批数据其实**不是独立的** ——
第 i 局在两次里用的是同一颗种子、同一份相位打散。同一个局面跑两遍，
一遍带新逻辑一遍不带，得到的是**配对样本**，判读力比两组独立采样高得多：
只要 12 局里翻了几局、翻向哪边，方向就是清楚的。

这就是本脚本干的事：同一批种子各跑一遍，逐局比较，再对"只有一边赢"的
局数做 McNemar 精确检验（翻盘 8:4 和 8:8 意思完全不同）。

跑法：
    python scripts/ab_level.py --level 0 --runs 96 --seed 31337
    python scripts/ab_level.py --level 0 --runs 96 --off belief   # 只关转述
    python scripts/ab_level.py --level 0 --runs 96 --off fan      # 只关分工

输出的"翻盘 a:b"是 McNemar 的 b/c：
    a = 关掉新逻辑才赢的局（新逻辑让它输了）
    b = 打开新逻辑才赢的局（新逻辑让它赢了）
"""
import argparse
import math
import os
import random
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
from sneak_ai.core.world import World  # noqa: E402
from sneak_ai.entity.enemy import Enemy  # noqa: E402


# ---------------------------------------------------------------- 开关
def _disable_belief():
    """关掉"转述"：报警只把可疑度抬起来，不给坐标，也不给方向（v1.8.5 的行为）。"""
    Enemy.note_belief = lambda self, pos, heading=None: None


def _disable_fan():
    """关掉"分工搜索"：各人按自己的记忆点搜，扇形不发。"""
    World._assign_search_slots = lambda self, dt: None


def _disable_post_rule():
    """关掉"固定岗不接转述"：让单点岗也吃转述、也拿搜索位。

    这一条存在的唯一原因是留个可复现的对照：它是第 5 关从 51.0% 掉到 32.3%
    的那一版。改 AI 的时候最容易说"我加了个更聪明的行为"，而这一条证明
    有些"更聪明"必须被挡在门外 —— 否则关卡的设计动词会被抵掉。
    """
    Enemy.is_fixed_post = property(lambda self: False)


SWITCHES = {"belief": _disable_belief, "fan": _disable_fan,
            "post": _disable_post_rule}

OFF_LABEL = {
    "belief+fan": "转述 + 分工（= 旧版）",
    "belief": "只有转述",
    "fan": "只有分工搜索",
    "post": "固定岗例外（= 出问题那版）",
}


def disable(spec: str) -> None:
    names = [s.strip() for s in spec.split("+") if s.strip()]
    for n in names:
        if n not in SWITCHES:
            raise SystemExit(f"不认识的开关 {n}（可选 {', '.join(SWITCHES)} / both）")
        SWITCHES[n]()


# ---------------------------------------------------------------- 检验
def mcnemar_exact(b: int, c: int) -> float:
    """只有一边赢的局数做精确二项检验（双侧）。

    为什么不用卡方版：翻盘的局数通常个位数，卡方近似在这里只是把噪声
    写成 p 值。精确版在小样本上保守，正好是"说不出狠话"该有的样子。
    """
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / (2 ** n)
    return min(1.0, 2 * tail)


def _one(level: int, rng, delay: float, loot: bool) -> dict:
    return botrun.run(level=level, rng=rng, start_delay=delay, loot=loot)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--level", type=int, default=0)
    ap.add_argument("--runs", type=int, default=96)
    ap.add_argument("--seed", type=int, default=31337)
    ap.add_argument("--jitter", type=float, default=1.0)
    ap.add_argument("--off", default="both",
                    help="要关掉的逻辑：belief / fan / both（默认）")
    a = ap.parse_args()

    lv = LEVELS[a.level]
    loot = lv.resource_critical
    print(f"=== A/B 配对：L{a.level + 1} {lv.name}，{a.runs} 局，seed={a.seed} ===")
    off_spec = "belief+fan" if a.off == "both" else a.off

    # 每局的相位由 (seed+i, jitter) 决定，两次跑必须用完全相同的种子序列。
    # 存成"种子值"而不是 Random 对象：复用同一个对象等于让第二次从
    # "已经抽过两次"的状态继续抽，相位序列就不同了，配对也就不成立。
    seeds = [a.seed + i for i in range(a.runs)]

    # 直接从类字典取原始对象再原样放回：`getattr` 拿到的是绑定方法/描述符的结果，
    # 塞回去会把方法变成普通属性，第二组就全错了。
    saved = {
        "note": Enemy.__dict__["note_belief"],
        "slots": World.__dict__["_assign_search_slots"],
        "post": Enemy.__dict__["is_fixed_post"],
    }
    off_rows = {}
    disable(off_spec)
    try:
        for i, sd in enumerate(seeds):
            rng = random.Random(sd)
            delay = rng.uniform(0.0, a.jitter)
            r = _one(a.level, rng, delay, loot)
            off_rows[i] = (r["state"], r["time"])
    finally:
        Enemy.note_belief = saved["note"]
        World._assign_search_slots = saved["slots"]
        Enemy.is_fixed_post = saved["post"]

    on_rows = {}
    for i, sd in enumerate(seeds):
        rng = random.Random(sd)
        delay = rng.uniform(0.0, a.jitter)
        r = _one(a.level, rng, delay, loot)
        on_rows[i] = (r["state"], r["time"])

    win = C.STATE_VICTORY
    both = sum(1 for i in off_rows
               if off_rows[i][0] == win and on_rows[i][0] == win)
    neither = sum(1 for i in off_rows
                  if off_rows[i][0] != win and on_rows[i][0] != win)
    only_off = sum(1 for i in off_rows
                   if off_rows[i][0] == win and on_rows[i][0] != win)
    only_on = sum(1 for i in off_rows
                  if off_rows[i][0] != win and on_rows[i][0] == win)

    n_off = sum(1 for i in off_rows if off_rows[i][0] == win)
    n_on = sum(1 for i in on_rows if on_rows[i][0] == win)
    p = mcnemar_exact(only_off, only_on)

    print(f"  关掉 {OFF_LABEL.get(off_spec, off_spec):<22} "
          f"{n_off:>3}/{a.runs} = {n_off / a.runs:5.1%}")
    print(f"  打开新逻辑       {n_on:>3}/{a.runs} = {n_on / a.runs:5.1%}")
    print(f"  配对：两边都过 {both} / 两边都败 {neither} / "
          f"只有关掉才过 {only_off} / 只有打开才过 {only_on}")
    print(f"  翻盘 {only_off}:{only_on}   McNemar 精确检验 p = {p:.3f}")
    if only_off + only_on == 0:
        print("  结论：一局都没翻 —— 这条逻辑对本关的胜负没有任何影响。")
    elif p < 0.05:
        worse = only_off > only_on
        print(f"  结论：**显著**（p<0.05）—— 新逻辑让这一关"
              f"{'变难' if worse else '变简单'}了，不是噪声。")
    else:
        print(f"  结论：翻盘不够多（p={p:.2f}），分不出高低；"
              f"点估计差 {(n_on - n_off) / a.runs:+.1%} 只能写成"
              f"「没测出差异」，不能写成「没有变化」。")

    # 赢局用时的配对差：胜率没动，也可能"赢得更勉强"
    pairs = [(off_rows[i][1], on_rows[i][1]) for i in off_rows
             if off_rows[i][0] == win and on_rows[i][0] == win]
    if pairs:
        d = sum(o - n for o, n in pairs) / len(pairs)
        print(f"  都通过的 {len(pairs)} 局：用时差（关 - 开）均值 {d:+.1f} 秒"
              f"（正数 = 打开新逻辑后走得更快）")


if __name__ == "__main__":
    main()
