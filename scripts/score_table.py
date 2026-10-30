"""计分系统体检：几种"典型玩法"各跑一局，把得分摆在一起看差距。

为什么需要这个脚本：
总分只是个数字，「这个分数设计得好不好」没人能靠读代码看出来。
唯一能验证的方式是——让同一个机器人用不同的策略打同一张图，
看它们能不能拿到**明显不同**的分。如果贴脸冲刺、一路静默、
全程开枪三种玩法的得分都差不多，那说明：

- 要么分项之间互相抵消了（快扣分、静默加分，正好打平）；
- 要么某一项权重太大，把别的玩法全压死了（"只有一种正确打法"）。

这两种都是设计事故，而它们在单个数字里完全看不出来。

用法：
    SDL_VIDEODRIVER=dummy python scripts/score_table.py
    SDL_VIDEODRIVER=dummy python scripts/score_table.py --runs 3

退出码：0 = 分差拉开且排名符合直觉，1 = 分差塌缩（需要调权重）。
"""

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

from botrun import run  # noqa: E402

# 名字, botrun 参数, 期望分。期望分**不是**"我们希望它得多少"，
# 而是"这一局的用时/发现/击倒，按设计公式应该值多少" —— 也就是一张独立算出来的
# 小表。两者对不上，就说明代码里的公式和设计意图已经分家了。
#
# 为什么用绝对分数而不是"谁排第一"来验：
# 这台机器人已经学会了"绕开视线"，它跑出来的"冒险"档其实只被发现 3 次、
# 还比稳着走快 7 秒 —— 按设计它就**应该**拿高分（它确实是更漂亮的一局）。
# 硬要求"稳的那一档必须第一"，等于要求设计去迎合一个比较笨的机器人。
# 所以这里验的是公式本身：期望 = 1100 − 1.5×用时 − 45×发现 − 100×击倒 + 风格分。
# 期望分由 `expected()` 现算，改常数时它会跟着变，只有漏改公式才会挂。
PLAYSTYLES = [
    ("补给流（顺路捡）", dict(aware=True, loot=True)),
    ("静默流（不捡不杀）", dict(aware=True)),
    ("冒险流（为快冒险）", dict(aware=True, loot=True, weight=2.5)),
    ("火力流（开枪清场）", dict(aware=True, armed=True, loot=True)),
]

# 对账用的两个表：每次 run 推一条，最后逐条比。放模块级是为了让
# main() 保持"一件事一件事地写"，不用在循环里再塞状态。
EXPECTED: list = []
ACTUAL: list = []


def expected(time: float, spotted: int, kills: int, style: int) -> int:
    """设计意图的独立实现：和 world.World.base_score() 分开写，才能互相校验。"""
    from sneak_ai import constants as C
    t = min(time, C.SCORE_TIME_FREE) + \
        max(0.0, time - C.SCORE_TIME_FREE) * C.SCORE_TIME_OVER_RATE
    return max(0, round(C.SCORE_BASE - t * C.SCORE_TIME_PENALTY
                        - spotted * C.SCORE_SPOTTED_PENALTY
                        - kills * C.SCORE_KILL_PENALTY + style))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=1)
    a = ap.parse_args()

    print(f"\n{'玩法':<22}{'结果':<6}{'用时':>6}{'发现':>5}{'击倒':>5}"
          f"{'基础':>6}{'风格':>6}{'总分':>6}  风格分明细")
    print("-" * 104)
    rows = []
    EXPECTED.clear()
    ACTUAL.clear()
    for name, kw in PLAYSTYLES:
        agg = {"score": 0, "base": 0, "time": 0.0, "spotted": 0, "kills": 0,
               "win": 0}
        last_style = []
        for _ in range(a.runs):
            r = run(**kw)
            agg["score"] += r["score"]
            agg["base"] += r["base"]
            agg["time"] += r["time"]
            agg["spotted"] += r["spotted"]
            agg["kills"] += r["kills"]
            agg["win"] += r["state"] == "victory"
            last_style = r["style"]
            style_sum = sum(v for _, v in r["style"])
            EXPECTED.append(expected(r["time"], r["spotted"], r["kills"], style_sum))
            ACTUAL.append(r["score"])
        n = a.runs
        win = agg["win"] > 0
        style_sum = sum(v for _, v in last_style)
        style_txt = "、".join(f"{k}+{v}" for k, v in last_style) or "（无）"
        print(f"{name:<22}{'通关' if win else '失败':<6}{agg['time'] / n:>8.1f}"
              f"{agg['spotted'] / n:>7.1f}{agg['kills'] / n:>7.1f}"
              f"{agg['base'] // n:>8}{style_sum:>8}{agg['score'] // n:>8}  {style_txt}")
        rows.append((name, agg["score"] // n, win))

    print()
    wins = [s for _, s, w in rows if w]
    if len(wins) < 2:
        print("⚠️ 通关的玩法不足两种，无法判断分差（先看 botrun 是否被难度卡死）")
        return 1

    spread = max(wins) - min(wins)
    hi = max(rows, key=lambda r: r[1] if r[2] else -1)
    print(f"通关玩法之间的最高分差：{spread} 分   最高：{hi[0]}（{hi[1]} 分）")
    if spread < 80:
        print("⚠️ 分差塌缩：不同玩法几乎拿一样的分，风格分没起作用")
        return 1
    print("✅ 玩法之间有可感知的分数差距")

    # 逐局对账：实际得分必须等于"按设计公式独立算出来"的分。
    # 这一条抓的是**公式实现和设计意图分家**——权重改了、封顶逻辑改了、
    # 失败局的零分规则漏了，只要两边不一致就挂。
    bad = 0
    for (name, score, win), exp, act in zip(rows, EXPECTED, ACTUAL):
        if not win:
            continue
        if abs(exp - act) > 1:
            print(f"❌ {name}：期望 {exp} 分，实际 {act} 分（差 {act - exp}）")
            bad += 1
    if bad:
        print("计分公式与设计意图不一致，检查 base_score / score / style_bonus")
        return 1
    print("✅ 每局的得分都与设计公式一致")

    # 失败局必须是 0 分。这条以前是真出过 bug：结算页不显示分数，
    # 但 score() 照样返回 827 分，存档和脚本都能看见 —— 失败的局拿全场最高分。
    if any(s != 0 for _, s, w in rows if not w):
        print("❌ 有失败的玩法拿到了非零分")
        return 1
    print("✅ 失败局得分为 0")
    return 0


if __name__ == "__main__":
    sys.exit(main())
