"""出第 3 关的整张地图俯视图（itch 第 3 张截图）。

原来的 screenshot-3 是手截的 760x604，和前两张 1280x960 混在一起，
itch 的 gallery 会按各自比例铺开 —— 宽的占满一行、小的留一片空白。
这里直接按窗口尺寸重出一张，尺寸对齐。

用法：SDL_VIDEODRIVER=dummy python scripts/make_map_shot.py [--lang en]
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pygame  # noqa: E402

from sneak_ai.__main__ import Game  # noqa: E402


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    lang = "en" if "--lang" in argv and argv[argv.index("--lang") + 1:] == ["en"] else "zh"
    level = 2  # 第 3 关：有掩体分区、路线最"像一张地图"
    if "--level" in argv:
        level = int(argv[argv.index("--level") + 1])

    from sneak_ai.core import i18n, settings
    original = settings.load_lang
    settings.load_lang = lambda: lang
    i18n.set_lang(lang)
    suffix = "" if lang == "zh" else "_en"

    pygame.init()
    g = Game(start_level=level)
    g.debug.enabled = False
    # 跑两帧：一帧把世界推进起来（不然相机还停在初始化位置），
    # 一帧画全图。Tab 全图是覆盖整个视口的，不依赖相机。
    g.world.update(1 / 60)
    g.cam.follow(g.player.x, g.player.y, 1.0)
    g.show_map = True
    g._render(1 / 60)
    out = ROOT / "assets" / "itch" / f"screenshot-3{suffix}.png"
    pygame.image.save(g.screen, str(out))

    fm = g.fullmap
    print(f"已生成 {out.relative_to(ROOT)}  {g.screen.get_width()}x{g.screen.get_height()}"
          f"  关卡={level + 1}  地图={fm.cols if hasattr(fm, 'cols') else '?'}x"
          f"{fm.rows if hasattr(fm, 'rows') else '?'}")
    settings.load_lang = original
    pygame.quit()


if __name__ == "__main__":
    main()
