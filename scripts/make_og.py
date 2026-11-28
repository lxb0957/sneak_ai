"""生成社交分享卡片 web/og.png（1200x630）。

别人把链接丢到群里 / 论坛时，没有 og:image 就是一条干巴巴的 URL，几乎没人点。
这里把菜单截图拼成标准 1.91:1 的横图，左边放标题。

    python scripts/make_og.py
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
# 源图优先级：先用"有冲突的一帧"（dist/og-frame.png，从演示片里抽的），
# 没有就退回旧的菜单截图。分享卡是别人点不点进来的唯一变量，
# 一张"三个人正在追你"的图，比一张主菜单好看得多。
SRC = ROOT / "dist" / "og-frame.png"
SRC_FALLBACK = ROOT / "dist" / "web-static.png"
OUT = ROOT / "web" / "og.png"
# 落地页目录里也放一份：发布脚本是从 web/landing 整目录同步的，
# 只写 web/og.png 的话线上还是上一版的图（踩过一次）。
OUT_ALT = ROOT / "web" / "landing" / "og.png"

W, H = 1200, 630
SHOT_W = 840  # 1280x960 正好 4:3，缩到 840x630 不变形
FONT_CANDIDATES = [
    "C:/Windows/Fonts/msyhbd.ttc",
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    str(ROOT / "assets" / "fonts" / "game-zh-subset.ttf"),
]

BG = (10, 16, 28)
ACCENT = (94, 234, 212)
TITLE = (232, 240, 250)
SUB = (140, 162, 190)


def font(size: int) -> ImageFont.FreeTypeFont:
    for p in FONT_CANDIDATES:
        if Path(p).is_file():
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def main() -> int:
    canvas = Image.new("RGB", (W, H), BG)

    src_path = SRC if SRC.is_file() else SRC_FALLBACK
    if not src_path.is_file():
        print(f"缺少截图：{SRC} / {SRC_FALLBACK}")
        return 1
    src = Image.open(src_path).convert("RGB")
    shot = src.resize((SHOT_W, H), Image.LANCZOS)
    canvas.paste(shot, (W - SHOT_W, 0))

    d = ImageDraw.Draw(canvas)
    # 左边文字区：360px
    d.rectangle([0, 0, W - SHOT_W, H], fill=BG)
    d.rectangle([W - SHOT_W - 2, 0, W - SHOT_W, H], fill=(28, 42, 62))

    x = 48
    d.text((x, 186), "潜行协议", font=font(54), fill=TITLE)
    d.text((x, 262), "Stealth Protocol", font=font(24), fill=ACCENT)
    d.text((x, 326), "会看、会听、会喊人", font=font(19), fill=SUB)
    d.text((x, 356), "十七个哨兵各走各的", font=font(19), fill=SUB)
    d.text((x, 424), "打开就能玩 · 不用下载", font=font(18), fill=SUB)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(OUT, "PNG", optimize=True)
    OUT_ALT.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(OUT_ALT, "PNG", optimize=True)
    print(f"已生成 {OUT} / {OUT_ALT}  {canvas.size[0]}x{canvas.size[1]}  "
          f"{OUT.stat().st_size / 1024:.0f} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
