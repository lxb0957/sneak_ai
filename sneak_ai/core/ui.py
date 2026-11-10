"""UI 基础设施：字体缓存 + 带缓存的文本绘制。

为什么要专门做这个：
`pygame.font.SysFont()` 每次调用都会去系统字体表里查一遍字体，
原实现每帧在 HUD 里调 6 次、每个敌人标签又调 1 次（18 个敌人 = 24 次/帧），
在 Python 里这是实打实的开销。字体只用缓存，文本只在"内容变化时"重渲染，
是本轮性能优化里最直接的一刀。

## 字体回退链（v1.8.10 补的账）

以前这里写死了三个 Windows 字体名：`microsoftyahei / msyh / simhei`。
在 Windows 上没问题，换个系统就是**全屏方块** —— pygame 找不到名字会退回
默认位图字体，那个字体没有中文字形，于是所有中文都渲染成豆腐块。
而 CI 的 `test-suite.yml` 里早就有这么一条注释："装了 Noto CJK，
让字体回退链在 CI 上也落到一个中文字体上" —— **可代码里根本没有回退链**，
注释描述的是一个不存在的机制。这类"注释比代码乐观"的坑这个项目踩过好几次
（黑灯 14 秒 vs 10 秒、哨位 14 个 vs 11 个），所以这回直接把链补上。

修法是**只把候选名单变长**，调用方式不动：`SysFont` 本来就按顺序找第一个
存在的名字，所以 Windows 上仍然命中 "microsoftyahei"、拿到同一个 Microsoft YaHei
字面（含真 Bold），**渲染逐像素不变**，四档窗口的布局审计和 README 截图都不用动。
（试过改成 `Font(match_font(...))` 按文件路径加载：Windows 上会解析到
`msyhl.ttc` = 雅黑 Light，粗体只能合成，20 号字宽 141→153，全站排版跟着变。
为一个跨平台补丁去改全站字体，代价远大于收益。）

`font_file()` 是这条链的**探针**：`match_font` 找不到返回 None（不像 SysFont
那样静默退回默认字体），所以它能回答"这台机器到底有没有中文字体"。
CI 和 `test_w35` 用它断言 —— 否则字体缺失的表现是"中文变方块"，
而方块不会让任何测试变红。
"""

import sys

import pygame

from .. import constants as C
from .i18n import t

_FONT_CACHE = {}

# 按平台排的候选名：全部小写、去空格（SysFont 内部就是这么比的）。
# 顺序有讲究：Windows 的放最前，保证 Windows 上的命中结果和历史版本一致。
_CJK_FONTS = (
    # Windows
    "microsoftyahei", "msyh", "simhei", "simsun", "dengxian",
    # macOS
    "pingfangsc", "hiraginosansgb", "stheiti", "heitisc",
    # Linux（Noto 是 Debian/Ubuntu 的默认中文包，文泉驿是老发行版的）
    "notosanscjksc", "notosanscjk", "notosanssc", "sourcehansanssc",
    "wenquanyimicrohei", "wqymicrohei", "wenquanyizenhei", "droidsansfallback",
    # 兜底：老 Windows / 部分精简系统上唯一带中日韩字形的字体
    "arialunicodems",
)

_font_file = None
_font_resolved = False


def font_file():
    """探针：这台机器上有哪个中文字体文件。返回 None = 一个都没有。

    只在测试和诊断里用，渲染路径不依赖它的返回值 —— 渲染走的是
    `SysFont(_CJK_FONTS, ...)`，行为和老版本完全一致。
    """
    global _font_file, _font_resolved
    if _font_resolved:
        return _font_file
    _font_resolved = True

    path = pygame.font.match_font(",".join(_CJK_FONTS))
    if path is None:
        # 名字对不上时（各发行版写法不一："Noto Sans CJK SC" / "NotoSansCJKsc"），
        # 再按关键字在已安装列表里猜一遍。猜错了也只是拿不到路径，
        # 渲染那边 SysFont 自己还会再找一遍。
        for name in pygame.font.get_fonts():
            if any(k in name for k in ("cjk", "hei", "song", "yahei",
                                       "pingfang", "sourcehansans", "wenquanyi")):
                path = pygame.font.match_font(name)
                if path:
                    break
    _font_file = path
    return path


def font(size: int, bold: bool = False) -> pygame.font.Font:
    key = (size, bold)
    f = _FONT_CACHE.get(key)
    if f is None:
        f = pygame.font.SysFont(list(_CJK_FONTS), size, bold=bold)
        _FONT_CACHE[key] = f
        if font_file() is None and len(_FONT_CACHE) == 1:
            # 只在**第一次**建字体时喊一声：每帧都喊等于没喊。
            print(f"[字体] 没找到中文字体（试过 {len(_CJK_FONTS)} 个名字），"
                  f"中文会显示成方块。Windows 自带雅黑、macOS 自带苹方，"
                  f"Linux 请装 fonts-noto-cjk。", file=sys.stderr)
    return f


def render(text: str, size: int, color, bold: bool = False) -> pygame.Surface:
    # 翻译只在这一道和 TextCache.get 里做：界面上的字全部先汇到这里，
    # 所以在绘制入口拦一次就够，199 个调用点一个都不用改。
    return font(size, bold).render(t(text), True, color)


class TextCache:
    """按 key 缓存文本表面：内容不变就不重渲染。

    key 用业务字段（如 "time"），value 用渲染源字符串；
    只有字符串变了才重新 render，避免每帧重建几十个 Surface。
    """

    def __init__(self, limit: int = 128):
        self._cache = {}
        self._limit = limit

    def get(self, key, text: str, size: int, color, bold: bool = False):
        # 先翻译再算签名：切语言时 text 变了，缓存自然失效，
        # 不需要在每个切换点上手动 clear()（漏一个就是"界面半中半英"）。
        text = t(text)
        item = self._cache.get(key)
        sig = (text, size, tuple(color), bold)
        if item is not None and item[0] == sig:
            return item[1]
        surf = render(text, size, color, bold)
        if len(self._cache) > self._limit:
            self._cache.clear()
        self._cache[key] = (sig, surf)
        return surf


_PANEL_CACHE = {}
_BAR_CACHE = {}


def draw_panel(screen, rect, alpha: int = 190, border: int = 1) -> None:
    """半透明圆角面板：HUD 的可读性基础（不让文字直接压在花花绿绿的地图上）。

    面板的内容**只由尺寸和透明度决定**，跟它画在屏幕哪个位置无关 ——
    所以整张成品可以缓存，每帧只剩一次 blit。HUD 每帧要画 6 块面板，
    原来是每块都新建 Surface + 画两个圆角矩形。
    """
    key = (rect.width, rect.height, alpha, border)
    surf = _PANEL_CACHE.get(key)
    if surf is None:
        # 面板尺寸实际只有十来种（HUD 六块 + 小地图 + 复盘行）。留个上限
        # 只是防将来有人拿动态尺寸调它，把缓存撑成内存泄漏。
        if len(_PANEL_CACHE) > 64:
            _PANEL_CACHE.clear()
        surf = pygame.Surface((rect.width, rect.height), pygame.SRCALPHA)
        local = surf.get_rect()
        pygame.draw.rect(surf, (*C.COL_HUD_PANEL, alpha), local, border_radius=8)
        pygame.draw.rect(surf, (*C.COL_HUD_LINE, 220), local, width=border,
                         border_radius=8)
        _PANEL_CACHE[key] = surf
    screen.blit(surf, rect.topleft)


def draw_bar(screen, rect, ratio: float, color, bg=(40, 46, 56), radius: int = 3) -> None:
    """进度条（体力 / 暴露度共用）。ratio 会被夹到 0~1。

    先画在一块独立的 alpha 面上再整体 blit：直接 `pygame.draw.rect(screen, ...)`
    的话，这条 12px 的条子对 UI 重叠审计是**不可见的** —— 审计只能看见
    blit 的落点，看不见直接绘制到画布上的图元。之前"暴露度条压住巡逻状态行"
    就是这么漏过去的。

    这块面每帧都会变（ratio 在动），所以不能缓存成品；但尺寸是固定的，
    复用同一张、清空重画即可 —— 省掉的是每帧一次 Surface 分配。
    """
    key = (rect.width, rect.height, radius)
    surf = _BAR_CACHE.get(key)
    if surf is None:
        surf = pygame.Surface((rect.width, rect.height), pygame.SRCALPHA)
        _BAR_CACHE[key] = surf
    surf.fill((0, 0, 0, 0))
    local = surf.get_rect()
    pygame.draw.rect(surf, bg, local, border_radius=radius)
    r = max(0.0, min(1.0, ratio))
    if r > 0:
        fill = pygame.Rect(0, 0, max(2, int(rect.width * r)), rect.height)
        pygame.draw.rect(surf, color, fill, border_radius=radius)
    pygame.draw.rect(surf, (70, 78, 92), local, width=1, border_radius=radius)
    screen.blit(surf, rect.topleft)
