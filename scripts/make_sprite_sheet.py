"""生成角色精灵展示图（assets/sprite_sheet.png），用来目视检查像素小人。

参考线是 32px 的格子，所以这张图能直接看出"人站在格子里有多大"。
跑法：
    python scripts/make_sprite_sheet.py
"""
import sys

sys.path.insert(0, ".")

import pygame

pygame.init()

from sneak_ai import constants as C
from sneak_ai.core.sprites import SpriteBank
from sneak_ai.core.ui import font as uifont

TILE = C.TILE_SIZE
CELL_W, CELL_H = 56, 76
PAD = 16
ROWS = [
    ("玩家 · 正面", C.COL_PLAYER_BODY, "front", False),
    ("玩家 · 背面", C.COL_PLAYER_BODY, "back", False),
    ("玩家 · 侧向", C.COL_PLAYER_BODY, "side", False),
    ("哨兵 · 正面", C.COL_ROLE["patrol"], "front", False),
    ("哨兵 · 背面", C.COL_ROLE["patrol"], "back", False),
    ("哨兵 · 侧向", C.COL_ROLE["patrol"], "side", False),
    ("追击 · 正面", C.COL_ROLE["chase"], "front", False),
    ("包抄 · 侧向", C.COL_ROLE["flank"], "side", False),
    ("玩家 · 蹲姿", C.COL_PLAYER_BODY, "front", True),
]
COLS = 6   # 帧 0..5（0 = 站姿，1..4 = 走，5 = 站姿重复）


def main():
    bank = SpriteBank()
    W = PAD * 2 + COLS * CELL_W + 150
    H = PAD * 2 + len(ROWS) * CELL_H + 40
    surf = pygame.Surface((W, H))
    surf.fill((30, 34, 40))
    # 地板棋盘格背景，方便判断对比度
    for gy in range(0, H, TILE):
        for gx in range(0, W, TILE):
            col = (34, 38, 44) if (gx // TILE + gy // TILE) % 2 else (30, 34, 40)
            pygame.draw.rect(surf, col, (gx, gy, TILE, TILE))

    font = uifont(15)
    small = uifont(13)
    for row, (label, color, group, crouch) in enumerate(ROWS):
        y = PAD + row * CELL_H
        surf.blit(font.render(label, True, (200, 210, 225)), (PAD, y + 18))
        for col in range(COLS):
            x = PAD + 150 + col * CELL_W
            # 用 facing 反推 group：front=90°, back=270°, side=0°
            facing = {"front": 1.5708, "back": -1.5708, "side": 0.0}[group]
            bank._anim_frame = col * 8 if col else 0
            spr = bank.sprite(color, facing, moving=(col > 0 and col < 5), crouch=crouch)
            if col == 5:
                spr = bank.sprite(color, facing, moving=False, crouch=crouch)
            ax, ay = bank.anchor
            rect = spr.get_rect(midbottom=(x + CELL_W // 2, y + 62))
            # 站立的格子参考线（一格 32px 宽的地面）
            pygame.draw.rect(surf, (52, 58, 68),
                             (x + CELL_W // 2 - TILE // 2, y + 62 - TILE, TILE, TILE), 1)
            pygame.draw.ellipse(surf, (0, 0, 0, 70),
                                (rect.centerx - 13, y + 52, 26, 12))
            surf.blit(spr, rect)
            if row == 0:
                surf.blit(small.render(str(col), True, (120, 130, 146)),
                          (x + CELL_W // 2 - 3, y + 64))

    pygame.image.save(surf, "assets/sprite_sheet.png")
    print("saved assets/sprite_sheet.png", surf.get_size())


if __name__ == "__main__":
    main()
