"""巡逻体检：跑一段时间，打印每个哨兵走了多远、最长原地不动多久。

"卡墙"的本质就是最后一个数字很大 —— 一个哨兵站在原地 20 秒，
玩家一眼就看出来这是个坏了的东西。这个脚本不判断游戏好不好玩，
只负责把"谁不动了"揪出来。

跑法：
    SDL_VIDEODRIVER=dummy python scripts/diag_patrol.py [秒数]
"""
import math
import sys

sys.path.insert(0, ".")

import pygame

pygame.init()

from sneak_ai.__main__ import Game


def main(seconds: float = 60.0) -> None:
    g = Game()
    w = g.world
    # 把玩家从世界里摘掉，让哨兵只受"巡逻"行为驱动。
    #
    # 旧版是把玩家挪到地图角落 —— 新地图改成实心开挖后，那个"角落"正好
    # 落在 G5 房间中央，哨兵一进门就把玩家抓住，state 变成 DEFEAT，
    # 而 World.update 在结算状态下会整体冻结，于是 22 个哨兵全部"卡住"。
    # 那次误报恰恰说明：测"巡逻"就不该让玩家在场。
    w.player = None

    tracks = [
        {"e": e, "x": e.x, "y": e.y, "dist": 0.0, "still": 0.0, "worst": 0.0,
         "worst_state": "", "prev": (e.x, e.y)}
        for e in w.enemies
    ]
    dt = 1 / 60
    steps = int(seconds / dt)
    for _ in range(steps):
        w.update(dt)
        for t in tracks:
            e = t["e"]
            d = math.hypot(e.x - t["prev"][0], e.y - t["prev"][1])
            t["dist"] += d
            t["prev"] = (e.x, e.y)
            if d < 0.004:
                t["still"] += dt
                if t["still"] > t["worst"]:
                    t["worst"] = t["still"]
                    t["worst_state"] = e.state
            else:
                t["still"] = 0.0

    print(f"巡逻体检：{seconds:.0f} 秒，理论最大位移约 {seconds * 2.2:.0f} 格")
    print("=" * 62)
    bad = 0
    for i, t in enumerate(tracks):
        e = t["e"]
        flag = ""
        if t["worst"] > 3.0:
            flag = "  <== 卡住"
            bad += 1
        print(
            f"#{i:02d} ({e.x:5.1f},{e.y:5.1f}) 巡逻点 {len(e.patrol)} 个 "
            f"走了 {t['dist']:6.1f} 格  最长不动 {t['worst']:5.1f}s"
            f"{'(' + t['worst_state'] + ')' if t['worst_state'] else ''}{flag}"
        )
    print("=" * 62)
    print(f"卡住的哨兵：{bad} / {len(tracks)}")


if __name__ == "__main__":
    main(float(sys.argv[1]) if len(sys.argv) > 1 else 60.0)
