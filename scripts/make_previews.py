"""生成 README 用的两张预览图（玩家视角 + F1 调试视角）。

不是摆拍：直接跑机器人策略，等到"有好几个哨兵在附近、有人正在起疑"
的那一刻截图 —— 这时候画面信息量最大（视野锥、暴露条、状态气泡都在动）。

用法：SDL_VIDEODRIVER=dummy python scripts/make_previews.py
"""
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import pygame  # noqa: E402

from sneak_ai import constants as C  # noqa: E402
from sneak_ai.__main__ import Game  # noqa: E402

import botrun as bot  # noqa: E402


def _drive(g, frames, stop_when=None):
    """按机器人策略推进；stop_when(world) 返回 True 时提前停。"""
    w, p = g.world, g.player
    danger = bot._danger_map(w)
    target = (int(C.DATA_TERMINAL[0]), int(C.DATA_TERMINAL[1]))
    path = bot._plan(w, (p.x, p.y), target, danger) or []
    pi, replan = 0, 0.0
    dt = 1 / 60
    for _ in range(frames):
        replan -= dt
        if replan <= 0.0:
            danger = bot._danger_map(w)
            path = bot._plan(w, (p.x, p.y), target, danger) or []
            pi, replan = 0, 0.4
        # 有人正看着 / 贴得很近就蹲着等，别一头撞上去 ——
        # 这段驱动只为了"活到画面好看的那一刻"，不是完整的机器人策略。
        watched = any(e.sees_player for e in w.enemies)
        close = any(math.hypot(e.x - p.x, e.y - p.y) < 3.5 for e in w.enemies)
        if watched or close:
            p._keys = {pygame.K_LSHIFT}
        elif pi < len(path):
            tx, ty = path[pi][0] + 0.5, path[pi][1] + 0.5
            if (tx - p.x) ** 2 + (ty - p.y) ** 2 < 0.16:
                pi += 1
            else:
                near = danger.get((int(p.x), int(p.y)), 0.0) > 0.25
                p._keys = bot._keys_toward(p, tx, ty, sneak=near)
        w.update(dt)
        # 必须同步驱动相机：只在最后渲染一帧的话，相机还停在出生点，
        # 截出来就是"玩家凭空消失"（画面跟的是地图另一头）。
        g.cam.follow(p.x, p.y, dt)
        # 贴脸了就停：这段驱动是"闷头走向终端"的直球策略，不会逃跑，
        # 再跑两帧就变成结算画面了（截图要的是游戏进行中）。
        if any(math.hypot(e.x - p.x, e.y - p.y) < 2.6 for e in w.enemies):
            return True
        if stop_when and stop_when(w):
            return True
    return False


def main():
    pygame.init()
    g = Game()
    g.debug.enabled = False

    def nice(w, cam):
        """想要的那一帧：玩家在动（噪音圈看得见）、附近有两三个哨兵、
        但没人贴脸也没人锁定 —— 信息量够，又不像是"马上就要被抓"。"""
        p = w.player
        near = [e for e in w.enemies
                if (e.x - p.x) ** 2 + (e.y - p.y) ** 2 < 11 ** 2]
        if len(near) < 2 or not p.moving or w.threat > 0.55:
            return False
        if any(math.hypot(e.x - p.x, e.y - p.y) < 4.0 for e in near):
            return False
        visible = 0
        for e in near:
            sx, sy = cam.world_to_screen(e.x, e.y)
            if -32 < sx < C.WINDOW_W + 32 and -32 < sy < C.WINDOW_H + 32:
                visible += 1
        return visible >= 2

    met = _drive(g, 60 * 30, lambda w: nice(w, g.cam))
    g._render(1 / 60)
    pygame.image.save(g.screen, str(ROOT / "assets" / "gameplay.png"))

    # 同一时刻切到 F1 调试视角
    g.debug.enabled = True
    g._render(1 / 60)
    pygame.image.save(g.screen, str(ROOT / "assets" / "ai_debug_view.png"))

    print(f"已生成 assets/gameplay.png 与 ai_debug_view.png"
          f"（t={g.world.elapsed:.1f}s，条件{'命中' if met else '未命中'}，"
          f"可见敌人 {g.world.seeing} 个，暴露度 {g.world.threat:.2f}）")
    pygame.quit()


if __name__ == "__main__":
    main()
