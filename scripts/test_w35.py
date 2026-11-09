"""W35：结账 —— 三笔"没结完的账"结清之后，把它们钉住。

## 为什么需要这个

`docs/KNOWN_ISSUES.md` 把三件事写成了"已经查过、决定不改"：第 4 关的 25%、
窄段两侧没有掩体、二进制只发 Windows。**账结清了不等于不会再犯** ——
真正会把账翻出来的有三种改法，而这三种都不会让任何现有测试变红：

1. **删掉跨平台字体候选**（"反正我这儿只有雅黑"）→ 换台机器全屏中文方块，
   而方块不会让任何断言失败；
2. **往掩体表里加一块落在收窄段墙行里的坐标** → `add_cover` 静默跳过，
   地图不变、截图不变、26 块配置实画 19 块的状态无声复活；
3. **动一下 `SWITCH_NARROW`** → 已经删掉的那 7 块死配置会突然"复活"成真墙，
   地形变了，而这次没有任何坐标改动可以 review。

所以这组测试守四件事：

- **字体真的能画中文**（不是"找到了一个字体名"，是"画出来的字不是两个一样的方块"）；
- **候选链覆盖三个平台**（Windows / macOS / Linux 各有代表，删一个就红）；
- **第 4 关的地形一个字节都没动**（sha256 钉死，删死配置前后一致的证明）；
- **没有掩体配置落在收窄段的墙行里**（静态检查，`add_cover` 那个静默跳过的坑）。

用法：
    python scripts/test_w35.py
"""

from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame  # noqa: E402

pygame.init()

from sneak_ai.core import levels as L  # noqa: E402
from sneak_ai.core import ui  # noqa: E402

# 五关地形的 sha256 前 16 位。L4 这一个数是 `KNOWN_ISSUES.A2` 的物证：
# 删掉 7 块从未落地的掩体配置之后，地形必须一字不差。
# 换图的时候改这张表 —— 改之前先想一遍"我是想改图，还是不小心改了图"。
TERRAIN_SHA = {
    "哨站": "62ccb965ce1b9384",
    "环形枢纽": "326b932daeb31acd",
    "柱林中庭": "5b9f320f150e153f",
    "配电所": "6d9c4e47d33351e6",
    "查验站": "341bcf0b342e985b",
}


def _terrain_sha(m) -> str:
    return hashlib.sha256(bytes(v for row in m.tiles for v in row)).hexdigest()[:16]


def test_font_chain_covers_three_platforms():
    """候选链必须三个平台都有代表。

    以前的实现只有 Windows 那三个名字，注释里却写着"回退链" ——
    注释比代码乐观，于是换台机器就是全屏方块。
    """
    names = set(ui._CJK_FONTS)
    need = {
        "Windows": {"microsoftyahei", "simhei"},
        "macOS": {"pingfangsc", "hiraginosansgb"},
        "Linux": {"notosanscjksc", "wenquanyimicrohei"},
    }
    for plat, want in need.items():
        missing = want - names
        assert not missing, f"{plat} 的候选被删了：{missing}（换机器就是全屏方块）"
    # 名字必须已经规范化：SysFont 内部把 "Microsoft YaHei" 比成 "microsoftyahei"，
    # 带空格或大写的条目永远匹配不上，写了等于没写。
    for n in ui._CJK_FONTS:
        assert n == n.lower() and " " not in n, f"字体名没规范化：{n!r}"
    print(f"[OK] 字体候选链覆盖三平台，{len(names)} 个名字全部规范化")


def _glyph_bytes(f, ch: str) -> bytes:
    """把字符画到不透明画布上再取像素。

    **不能直接** `pygame.image.tobytes(surface, "RGB")`：`render()` 出来的是
    逐像素 alpha 表面，直接转 RGB 会把整张图读成一片 0 —— 于是四个不同的汉字
    得到四份一模一样的字节，测试就会煞有介事地宣布"中文是方块"。
    （真踩过：第一次写这条断言，它报的是"潜=验"，而屏幕上明明是两个字。）
    先 blit 到不透明画布上，读到的才是合成之后的真实像素。
    """
    s = f.render(ch, True, (255, 255, 255))
    canvas = pygame.Surface(s.get_size())
    canvas.fill((0, 0, 0))
    canvas.blit(s, (0, 0))
    return pygame.image.tobytes(canvas, "RGB")


def test_font_actually_draws_chinese():
    """找到字体 ≠ 能画中文。

    真正要验的是字形存在：两个不同的汉字必须渲染出两张不同的位图。
    缺字形时字体引擎会画统一宽度的"豆腐块"，两个字**逐像素相同** ——
    这是唯一一个不依赖具体字体、又能当场判定"中文是不是方块"的判据。
    """
    path = ui.font_file()
    assert path, ("这台机器上一个中文字体都没有。CI 上请确认装了 fonts-noto-cjk；"
                  "Linux 桌面请装 fonts-noto-cjk 或文泉驿。")
    f = ui.font(20)
    a, b = _glyph_bytes(f, "潜"), _glyph_bytes(f, "验")
    assert len(a) == len(b), "两个汉字的位图尺寸都不一样，肯定不是同一个字体状态"
    assert a != b, "两个汉字渲染成了同一张位图 —— 那是缺字形的豆腐块，不是中文"
    # 顺带验一下它认得拉丁字母：同一个字体对象里中文和西文都要有。
    assert _glyph_bytes(f, "潜") != _glyph_bytes(f, "A"), "中英文渲成了同一张图"
    print(f"[OK] 中文字形可用：{Path(path).name}（潜≠验≠A，不是方块）")


def test_terrain_unchanged():
    """五关地形 sha256 与记录一致 —— 删死配置那次必须一字未动。"""
    for i, lv in enumerate(L.LEVELS):
        got = _terrain_sha(lv.build())
        want = TERRAIN_SHA[lv.name]
        assert got == want, (f"{lv.name}（第 {i + 1} 关）地形变了：{got} != {want}"
                             f"；如果是有意改图，更新 TERRAIN_SHA 并说明为什么")
    print("[OK] 五关地形 sha256 与记录一致（删掉 7 块死掩体没有动图）")


def test_no_cover_block_in_narrow_band():
    """掩体配置不许落在收窄段填掉的墙行里。

    `add_cover` 的规则是"整块里只要有一格是墙就整块跳过"，静默的。
    这条静态检查就是那个静默行为的天敌：**配置表和收窄段在数据上不许打架**，
    而不是等画出来之后靠人数。
    """
    # 与 build_switchyard 里那三个 fill 一一对应（y=20 / y=25,26 / y=33）
    walled = set()
    for (x0, x1) in L.SWITCH_NARROW:
        for x in range(x0, x1 + 1):
            walled.update({(x, 20), (x, 25), (x, 26), (x, 33)})

    blocks = L.switchyard_cover_blocks()
    assert len(blocks) == 19, (f"掩体表有 {len(blocks)} 块，记录是 19 块；"
                               f"加块是好事，但请同时确认它真的落地了（见下一条断言）")
    bad = []
    for (bx, by, bw, bh) in blocks:
        hit = {(x, y) for x in range(bx, bx + bw) for y in range(by, by + bh)} & walled
        if hit:
            bad.append(((bx, by, bw, bh), sorted(hit)))
    assert not bad, f"这些掩体块跨进了收窄段的墙行，会被 add_cover 静默跳过：{bad}"
    print(f"[OK] {len(blocks)} 块掩体全部落在收窄段墙行之外（不会静默消失）")


def test_accounts_page_exists_and_linked():
    """账本必须在，而且 README 里找得到 —— 写在没人看的地方等于没写。"""
    doc = ROOT / "docs" / "KNOWN_ISSUES.md"
    assert doc.exists(), "docs/KNOWN_ISSUES.md 不见了"
    text = doc.read_text(encoding="utf-8")
    for key in ("A1", "A2", "B1"):
        assert key in text, f"账本里缺 {key} 这一条"
    for word in ("25%", "掩体", "win64"):
        assert word in text, f"账本里缺关键词「{word}」"
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "KNOWN_ISSUES" in readme, "README 没链到账本 —— 它在仓库深处等于不存在"
    print("[OK] 账本存在且被 README 链到，三笔账都在")


def main() -> int:
    test_font_chain_covers_three_platforms()
    test_font_actually_draws_chinese()
    test_terrain_unchanged()
    test_no_cover_block_in_narrow_band()
    test_accounts_page_exists_and_linked()
    print("\nW35 全部通过：三笔账结清了，而且钉住了")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
