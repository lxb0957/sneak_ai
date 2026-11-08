"""辅助模式到底改了什么：**配对**测量，不是"调了参数应该更好"。

为什么必须配对。第 4 关的败局大头是"开局相位运气"——同一张图，哨兵恰好
面对你的那一次进门就是死。两组各跑 60 局、比两个点估计，差 10 个点也说不清
是辅助的功劳还是抽签抽得好。这里让两组共用同一批种子（seed..seed+runs-1），
只比较**同一批开局里翻盘了多少局**，用 McNemar 精确检验（只看 b、c 两格）。

为什么不和 README 里"第 4 关 25%"比。那 25% 是 `measure_levels.py` 的尺子：
前四关 loot=off、不带枪、按 `resource_critical` 决定捡不捡。这里量的是另一个
问题——"辅助模式对一局完整玩法的影响"，所以带枪、捡补给。**两把尺子量的
不是同一个东西，数字不能并排放。** 想复现那条 25% 用 measure_levels.py。

跑法：
    python scripts/measure_assist.py                 # 第 4 关，各 60 局
    python scripts/measure_assist.py --runs 120 --level 3

单进程约 25 局/分钟（每局都真跑到 200 秒上限或通关），60 局配对各跑一遍
约 5 分钟。

## 判读

- **胜率差**看 McNemar 的 p，不看两个边际区间的重叠情况（原因见
  measure_levels.py 顶部那段）；
- **胜局均时变长是预期内的**，不是"辅助让人打得更慢"：多出来的那些局原本
  是败局（被看见 → 死了），现在变成"被看见 → 跑掉 → 绕远路躲回去 → 通关"。
  填的是"活下来"，不是"变快"。
"""

import argparse
import random
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sneak_ai.core import records, settings  # noqa: E402

# 偏好文件指到临时目录：量尺不能顺手改玩家存档里的辅助开关。
_TMP = Path(tempfile.mkdtemp())
settings.DATA_DIR = _TMP
settings.SETTINGS_FILE = None
records.DATA_DIR = _TMP
records.RECORD_FILE = None

import scripts.botrun as botrun  # noqa: E402
from scripts.measure_levels import mcnemar_exact, wilson  # noqa: E402
from sneak_ai.__main__ import Game  # noqa: E402
from sneak_ai.core.levels import LEVELS  # noqa: E402


def one_run(assist: bool, level: int, seed: int, budget: float) -> dict:
    """跑一局，返回 botrun 那份 dict。每组都新建 Game() —— 辅助是建世界时
    乘进敌人参数的，复用同一个 Game 会让第二组继承第一组的世界。"""
    settings.save(assist=assist)
    g = Game()
    assert g.assist is assist, (g.assist, assist)
    g.level_index = level
    g._carry = {}
    g._new_world()
    return botrun.run(game=g, level=level, rng=random.Random(seed),
                      budget=budget, armed=True, loot=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--level", type=int, default=3, help="0 基关号，默认 3 = 第 4 关")
    ap.add_argument("--runs", type=int, default=60)
    ap.add_argument("--seed", type=int, default=1000)
    ap.add_argument("--budget", type=float, default=200.0)
    a = ap.parse_args()

    lv = LEVELS[a.level]
    print(f"第 {a.level + 1} 关「{lv.name}」  {a.runs} 局配对"
          f"（seed {a.seed}..{a.seed + a.runs - 1}，带枪 + 捡补给）")
    print("每一格 = 同一颗种子下 (关辅助, 开辅助) 的胜负\n")

    off_win, on_win = [], []
    off_time, on_time = [], []
    only_on = only_off = 0
    for i in range(a.runs):
        seed = a.seed + i
        r_off = one_run(False, a.level, seed, a.budget)
        r_on = one_run(True, a.level, seed, a.budget)
        w_off = r_off["state"] == "victory"
        w_on = r_on["state"] == "victory"
        off_win.append(w_off)
        on_win.append(w_on)
        if w_off:
            off_time.append(r_off["time"])
        if w_on:
            on_time.append(r_on["time"])
        if w_on and not w_off:
            only_on += 1
        elif w_off and not w_on:
            only_off += 1
        mark = {(True, True): "==", (False, False): "..",
                (False, True): "↑ ", (True, False): "↓ "}[(w_off, w_on)]
        print(f"  {seed:>5}  {mark}", end="\n" if (i + 1) % 6 == 0 else "")

    def line(name: str, flags, times):
        k, n = sum(flags), len(flags)
        lo, hi = wilson(k, n)
        avg = sum(times) / len(times) if times else 0.0
        # 区间用一位小数：59/60 的 Wilson 上界是 99.7%，写成 100% 就成了
        # "必胜"——那正是 wilson() 的注释在防的那种话。
        print(f"{name}  {k:>3}/{n} = {k / n:5.1%}  95%CI[{lo:.1%},{hi:.1%}]"
              f"  胜局均时 {avg:5.1f}s")

    print()
    line("关辅助", off_win, off_time)
    line("开辅助", on_win, on_time)
    print(f"翻盘  只有辅助赢 {only_on} 局 / 只有关着赢 {only_off} 局"
          f"   McNemar p = {mcnemar_exact(only_on, only_off):.2g}")
    print("\n↑ 只有辅助赢   ↓ 只有关着赢   == 都赢   .. 都输")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
