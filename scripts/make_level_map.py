"""把一关的整张地图画成一张俯视图（带目标点、补给、出生点标记）。

游戏里相机只给 40x30 格的视口，看一张 80x60 的图得跑起来才拼得出全貌。
这个脚本直接烘一张全图，用来在 README / BLOG 里讲"这一关长什么样"。

用法：
    SDL_VIDEODRIVER=dummy python scripts/make_level_map.py --level 1
输出：dist/maps/level{序号}.png（--out 可改）

画的是**真实地图数据**（`Level.build()` 出来的网格 + 引擎自己的 MapView 烘焙），
不是手绘示意图 —— 图上每一块砖都和跑起来看到的一致。
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pygame  # noqa: E402

from sneak_ai import constants as C  # noqa: E402
from sneak_ai.core import levels as L  # noqa: E402
from sneak_ai.core.mapview import MapView  # noqa: E402

FONT_CANDIDATES = (
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "/System/Library/Fonts/PingFang.ttc",
)

MARKERS = (
    # (取世界坐标的函数名, 颜色, 标签)
    ("spawn", (235, 235, 240), "出生"),
    ("terminal", (120, 240, 170), "数据终端"),
    ("exfil", (110, 180, 245), "撤离点"),
)
PICKUP_COLORS = {
    "ammo": (240, 200, 110),
    "stone": (200, 200, 210),
    "shield": (130, 200, 245),
}
PICKUP_NAMES = {"ammo": "弹药", "stone": "石子", "shield": "护盾"}


def _font(size: int):
    for path in FONT_CANDIDATES:
        if Path(path).exists():
            return pygame.font.Font(path, size)
    return pygame.font.SysFont(None, size)


def render(level: int, out_path: Path, width: int = 760) -> Path:
    pygame.init()
    pygame.display.set_mode((1, 1))
    lv = L.LEVELS[level]
    grid = lv.build()
    surf = MapView(grid).surface

    # 裁到"有地板的地方"，否则一圈实心岩体会占掉 1/3 画面
    floors = [(x, y) for y in range(grid.h) for x in range(grid.w)
              if not grid.is_wall(x, y)]
    x0 = max(0, min(f[0] for f in floors) - 1)
    x1 = min(grid.w - 1, max(f[0] for f in floors) + 1)
    y0 = max(0, min(f[1] for f in floors) - 1)
    y1 = min(grid.h - 1, max(f[1] for f in floors) + 1)

    ts = C.TILE_SIZE
    crop = pygame.Surface(((x1 - x0 + 1) * ts, (y1 - y0 + 1) * ts))
    crop.blit(surf, (0, 0), (x0 * ts, y0 * ts, crop.get_width(), crop.get_height()))
    scale = width / crop.get_width()
    img = pygame.transform.smoothscale(
        crop, (width, int(crop.get_height() * scale)))

    def to_px(wx: float, wy: float):
        return ((wx - x0) * ts * scale, (wy - y0) * ts * scale)

    # ---- 补给点：小圆点 ----
    for (px, py, kind) in lv.pickups:
        cx, cy = to_px(px + 0.5, py + 0.5)
        pygame.draw.circle(img, (10, 12, 16), (int(cx), int(cy)), 5)
        pygame.draw.circle(img, PICKUP_COLORS.get(kind, (255, 255, 255)),
                           (int(cx), int(cy)), 3)

    # ---- 三个关键点：实心圆 + 白边，保证在任何背景上都看得见 ----
    font = _font(15)
    placed = []
    for attr, color, label in MARKERS:
        wx, wy = getattr(lv, attr)
        cx, cy = to_px(wx, wy)
        pygame.draw.circle(img, (8, 10, 14), (int(cx), int(cy)), 9)
        pygame.draw.circle(img, color, (int(cx), int(cy)), 6)
        # 标签往画面中心方向推，避免被裁掉
        lx = cx + 14 if cx < img.get_width() * 0.6 else cx - 14
        text = font.render(label, True, color)
        rect = text.get_rect()
        rect.center = (int(lx + (rect.width // 2 if cx < img.get_width() * 0.6
                                else -rect.width // 2)), int(cy))
        # 文字阴影：地图是深色的，纯色字压在砖缝上会糊
        shadow = font.render(label, True, (0, 0, 0))
        img.blit(shadow, (rect.x + 1, rect.y + 1))
        img.blit(text, rect)

    # ---- 底部图例 ----
    bar_h = 34
    canvas = pygame.Surface((img.get_width(), img.get_height() + bar_h))
    canvas.fill((10, 12, 16))
    canvas.blit(img, (0, 0))
    small = _font(14)
    x = 12
    labels = [("出生", (235, 235, 240)), ("弹药", PICKUP_COLORS["ammo"]),
              ("石子", PICKUP_COLORS["stone"]), ("护盾", PICKUP_COLORS["shield"]),
              (f"{grid.w}x{grid.h} 格 · 外环 4 格宽", (150, 155, 165))]
    for label, color in labels:
        pygame.draw.circle(canvas, color, (x, img.get_height() + 17), 4)
        t = small.render(label, True, (205, 208, 215))
        canvas.blit(t, (x + 10, img.get_height() + 17 - t.get_height() // 2))
        x += 10 + t.get_width() + 22

    out_path.parent.mkdir(parents=True, exist_ok=True)
    pygame.image.save(canvas, str(out_path))
    pygame.quit()
    return out_path


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--level", type=int, default=0)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    out = Path(a.out) if a.out else (ROOT / "dist" / "maps" / f"level{a.level + 1}.png")
    p = render(a.level, out, 760)
    print(f"已生成 {p}（{L.LEVELS[a.level].name}）")
