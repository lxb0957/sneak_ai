"""给某一关拍实跑截图：和游戏内渲染走同一条路径，画面不是摆拍的。

为什么要有这个脚本：README / BLOG 里讲关卡设计的时候，光贴地图数据说服力
不够，"这个环是不是真的能绕过去"必须靠一张实跑的画面回答。而临时写的
截图脚本每次都要重新拼一遍 Game + botrun + 渲染，拼错了（比如忘了推进
`_time_accum`）就会拍到一堆静止帧 —— 所以做成脚本。

机器人用 `botrun.run(aware=True)`，和 `record_demo.py`、难度审计完全同一份
策略代码：改坏 botrun，这些截图也会跟着变差。

用法：
    SDL_VIDEODRIVER=dummy python scripts/shoot.py --level 1 --at 12,30
    SDL_VIDEODRIVER=dummy python scripts/shoot.py --level 0 --at 40

输出：dist/shots/lv{关卡号}_{秒}s.png（--out 可改）
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import pygame  # noqa: E402

from sneak_ai.__main__ import Game  # noqa: E402

import botrun as bot  # noqa: E402

FPS = 60
DT = 1.0 / FPS


def shoot(level: int, at: list, out_dir: Path, budget: float = 180.0) -> list:
    pygame.init()
    g = Game()
    g.debug.enabled = False
    if level:
        # 只拍第 level 关：直接切关重建，别让机器人赢了第 1 关后自己推进
        g.level_index = level
        g._carry = {}
        g._new_world()

    name = g.world.level_name
    out_dir.mkdir(parents=True, exist_ok=True)
    pending = sorted(at)
    saved = []
    tick = {"n": 0}

    def capture(t, game, _mode):
        # 动画相位必须自己推进：Game.run 里负责这事的代码不在渲染路径上，
        # 不推就会拍到脉动停住、脚步不动的"假暂停"画面。
        game._time_accum += DT
        game._render(DT)
        tick["n"] += 1
        if pending and t >= pending[0]:
            want = pending.pop(0)
            path = out_dir / f"lv{level + 1}_{int(want)}s.png"
            pygame.image.save(game.screen, str(path))
            saved.append(path)
            print(f"  t={want:5.1f}s  -> {path.name}  "
                  f"({game.world.state}, ({game.player.x:.1f},{game.player.y:.1f}))")

    result = bot.run(aware=True, game=g, budget=budget, on_frame=capture)
    print(f"{name}：{result['state']} 用时 {result['time']}s  "
          f"被发现 {result['spotted']} 次  {result['score']} 分 "
          f"{result['rank']} 评级")
    return saved


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--level", type=int, default=0, help="0=哨站 1=环形枢纽")
    ap.add_argument("--at", default="12,30", help="定格时间（秒），逗号分隔")
    ap.add_argument("--budget", type=float, default=180.0, help="机器人最多跑多久")
    ap.add_argument("--out", default=str(ROOT / "dist" / "shots"))
    a = ap.parse_args()
    shoot(a.level, [float(x) for x in a.at.split(",")], Path(a.out), a.budget)
