"""录制演示 GIF：一次真实的潜行通关。

**没有任何一段走位是写死的**：录像直接调用 `scripts/botrun.py` 里的
"看得懂视野锥的玩家"策略 —— 同一个 `run()`、同一份危险度地图、同一套
GO / FLEE / HIDE 状态机。所以 README 里那句"这段 GIF 不是摆拍"
是可验证的：把 `botrun.py` 改坏，这段录像也必然跟着变差。

（早期版本这里抄了一份策略代码，结果 botrun 后来改进（比如把"哨兵站岗
环视"算进危险度），录像用的还是旧逻辑，两边成绩对不上 —— 抄代码的代价。）

用法：python scripts/record_demo.py
输出：assets/demo.gif
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import pygame  # noqa: E402

from sneak_ai import constants as C  # noqa: E402,F401
from sneak_ai.__main__ import Game  # noqa: E402

import botrun as bot  # noqa: E402

FPS = 60
RECORD_EVERY = 14         # 每 14 帧取 1 帧 -> 约 4.3fps；像素风低帧率不影响看懂
GIF_SIZE = (432, 324)     # 输出尺寸（1280x960 的 34%）
GIF_COLORS = 48           # 调色板颜色数：像素风本来就色少，砍完几乎看不出差别
MAX_SECONDS = 120.0


def record(out_path=None):
    pygame.init()
    g = Game()
    out_path = out_path or (ROOT / "assets" / "demo.gif")

    # 关掉 F1 调试层：游戏自带的视野锥 + 小地图已经把 AI 状态讲清楚了，
    # 再叠一层调试线反而糊。
    g.debug.enabled = False

    screens = []
    dt = 1.0 / FPS
    tick = {"n": 0}

    def capture(_t, game, _mode):
        """每帧回调：正常渲染，每 RECORD_EVERY 帧存一张。"""
        game._time_accum += dt
        game._handle_audio(dt)
        game._render(dt)
        tick["n"] += 1
        if tick["n"] % RECORD_EVERY == 0:
            screens.append(game.screen.copy())

    result = bot.run(aware=True, game=g, budget=MAX_SECONDS, on_frame=capture)
    w = g.world

    # 结算页多定格一会儿，让观众看清评级和得分
    for i in range(int(1.6 * FPS)):
        g._time_accum += dt
        g._render(dt)
        if i % RECORD_EVERY == 0:
            screens.append(g.screen.copy())

    from PIL import Image

    pil_frames = []
    for s in screens:
        raw = pygame.image.tostring(s, "RGB")
        img = Image.frombytes("RGB", s.get_size(), raw)
        img = img.resize(GIF_SIZE, Image.LANCZOS)
        pil_frames.append(img.convert("P", palette=Image.ADAPTIVE, colors=GIF_COLORS))

    pil_frames[0].save(
        str(out_path),
        save_all=True,
        append_images=pil_frames[1:],
        duration=int(1000 / (FPS / RECORD_EVERY) * 0.75),   # 略微加速，观感更紧凑
        loop=0,
        optimize=True,
    )
    pygame.quit()
    print(f"已生成 {out_path}（{len(screens)} 帧，结局 {result['state']}，"
          f"用时 {w.elapsed:.1f}s，被发现 {w.spotted} 次，"
          f"击倒 {w.kills} 人，得分 {w.score()}）")
    return result


if __name__ == "__main__":
    record()
