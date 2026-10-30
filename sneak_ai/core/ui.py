"""UI 基础设施：字体缓存 + 带缓存的文本绘制。

为什么要专门做这个：
`pygame.font.SysFont()` 每次调用都会去系统字体表里查一遍字体，
原实现每帧在 HUD 里调 6 次、每个敌人标签又调 1 次（18 个敌人 = 24 次/帧），
在 Python 里这是实打实的开销。字体只用缓存，文本只在"内容变化时"重渲染，
是本轮性能优化里最直接的一刀。
"""

import pygame

from .. import constants as C

_FONT_CACHE = {}


def font(size: int, bold: bool = False) -> pygame.font.Font:
    key = (size, bold)
    f = _FONT_CACHE.get(key)
    if f is None:
        f = pygame.font.SysFont(["microsoftyahei", "msyh", "simhei"], size, bold=bold)
        _FONT_CACHE[key] = f
    return f


def render(text: str, size: int, color, bold: bool = False) -> pygame.Surface:
    return font(size, bold).render(text, True, color)


class TextCache:
    """按 key 缓存文本表面：内容不变就不重渲染。

    key 用业务字段（如 "time"），value 用渲染源字符串；
    只有字符串变了才重新 render，避免每帧重建几十个 Surface。
    """

    def __init__(self, limit: int = 128):
        self._cache = {}
        self._limit = limit

    def get(self, key, text: str, size: int, color, bold: bool = False):
        item = self._cache.get(key)
        sig = (text, size, tuple(color), bold)
        if item is not None and item[0] == sig:
            return item[1]
        surf = render(text, size, color, bold)
        if len(self._cache) > self._limit:
            self._cache.clear()
        self._cache[key] = (sig, surf)
        return surf


def draw_panel(screen, rect, alpha: int = 190, border: int = 1) -> None:
    """半透明圆角面板：HUD 的可读性基础（不让文字直接压在花花绿绿的地图上）。"""
    surf = pygame.Surface((rect.width, rect.height), pygame.SRCALPHA)
    pygame.draw.rect(surf, (*C.COL_HUD_PANEL, alpha), surf.get_rect(), border_radius=8)
    pygame.draw.rect(surf, (*C.COL_HUD_LINE, 220), surf.get_rect(), width=border,
                     border_radius=8)
    screen.blit(surf, rect.topleft)


def draw_bar(screen, rect, ratio: float, color, bg=(40, 46, 56), radius: int = 3) -> None:
    """进度条（体力 / 暴露度共用）。ratio 会被夹到 0~1。

    先画在一块独立的 alpha 面上再整体 blit：直接 `pygame.draw.rect(screen, ...)`
    的话，这条 12px 的条子对 UI 重叠审计是**不可见的** —— 审计只能看见
    blit 的落点，看不见直接绘制到画布上的图元。之前"暴露度条压住巡逻状态行"
    就是这么漏过去的。
    """
    surf = pygame.Surface((rect.width, rect.height), pygame.SRCALPHA)
    local = surf.get_rect()
    pygame.draw.rect(surf, bg, local, border_radius=radius)
    r = max(0.0, min(1.0, ratio))
    if r > 0:
        fill = pygame.Rect(0, 0, max(2, int(rect.width * r)), rect.height)
        pygame.draw.rect(surf, color, fill, border_radius=radius)
    pygame.draw.rect(surf, (70, 78, 92), local, width=1, border_radius=radius)
    screen.blit(surf, rect.topleft)
