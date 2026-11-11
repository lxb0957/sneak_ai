"""录制演示 GIF：一次真实的潜行通关。

**没有任何一段走位是写死的**：录像直接调用 `scripts/botrun.py` 里的
"看得懂视野锥的玩家"策略 —— 同一个 `run()`、同一份危险度地图、同一套
GO / FLEE / HIDE 状态机。所以 README 里那句"这段 GIF 不是摆拍"
是可验证的：把 `botrun.py` 改坏，这段录像也必然跟着变差。

（早期版本这里抄了一份策略代码，结果 botrun 后来改进（比如把"哨兵站岗
环视"算进危险度），录像用的还是旧逻辑，两边成绩对不上 —— 抄代码的代价。）

用法：
    python scripts/record_demo.py              # 第 1 关 -> assets/demo.gif
    python scripts/record_demo.py --level 1    # 第 2 关 -> assets/demo_lv2.gif
    python scripts/record_demo.py --level 4    # 第 5 关 -> assets/demo_lv5.gif
    python scripts/record_demo.py --mp4 --level 1   # 第 2 关 -> assets/demo_lv2.mp4

`--mp4` 是给发布用的：GIF 因为体积被压到 432x324，贴到 Reddit / X 上会被放大成一团糊，
而 H.264 在 960x720 下比 432 宽的 GIF 还小。帧直接流进 ffmpeg 的 stdin，不攒内存
（1280x960 现实一局 70 秒就是 10 GB，攒不下）。依赖 `pip install imageio-ffmpeg`
（自带 ffmpeg 二进制，不用系统装 ffmpeg）；游戏运行不需要它，所以不进 requirements.txt。

关卡全五关都支持（0~4），文件名按 `demo_lv{level+1}` 推，第 1 关就是 demo.gif。
`--phase` 只影响哨兵开局站位：同一关换个相位就是一个新局面，某条路线在这里能过、
在那里未必 —— README 里第 2 关那段 0.25 会输、0.2 能过，就是这么扫出来的。

为什么按关分开录、而不是录成一整局：两关合起来 120 秒，GIF 会翻倍到 15MB 上下，
README 打开就卡。分成两条反而更好读 —— 读者点进哪一节就看哪一关的实跑。
"""

import argparse
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
MP4_SIZE = (960, 720)     # mp4 输出尺寸：4:3 照原比例，发布站点放大两倍也不糊
MP4_EVERY = 2             # mp4 每 2 帧取 1 帧 -> 30fps（H.264 压得动，不用像 GIF 那样砍帧）
MAX_SECONDS = 120.0


def record(out_path=None, level=0, phase=0.0, clip=None, crop=None, every=None,
           mp4=False):
    pygame.init()
    g = Game()
    if level:
        # 只录指定关卡：直接切关重建，跨关统计清空 —— 否则机器人赢了第 1 关
        # 会触发关卡推进，录到的就不是这一关的画面了。
        g.level_index = level
        g._carry = {}
        g._new_world()
    ext = "mp4" if mp4 else "gif"
    out_path = out_path or (ROOT / "assets"
                            / (f"demo_lv{level + 1}.{ext}" if level else f"demo.{ext}"))

    # 关掉 F1 调试层：游戏自带的视野锥 + 小地图已经把 AI 状态讲清楚了，
    # 再叠一层调试线反而糊。
    g.debug.enabled = False

    screens = []
    writer = None
    if mp4:
        import subprocess

        import imageio_ffmpeg

        vw, vh = MP4_SIZE
        fps_out = FPS / (every or MP4_EVERY)
        cmd = [imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error",
               "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{vw}x{vh}",
               "-r", f"{fps_out:.3f}", "-i", "-",
               "-c:v", "libx264", "-preset", "medium", "-crf", "21",
               "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out_path)]
        # 帧直接流进 stdin：1280x960 一局 70 秒就是 10 GB，攒下来必爆内存
        writer = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    dt = 1.0 / FPS
    tick = {"n": 0}
    # 时间窗：只留 t0..t0+dur 这几秒。第 5 关一局 90~110 秒，整局录下来
    # GIF 会到十几 MB，而 README 只需要讲清"扔石头 → 他走开 → 你过去"这一个动作。
    t0, t1 = ((clip[0], clip[0] + clip[1]) if clip else (0.0, 1e9))
    # 抽帧间隔：默认 14 帧一张（约 4.3fps）。石子飞 0.4 秒就落地，
    # 这个间隔会整整漏掉它 —— 要讲"扔石头"的动作就得把间隔压小。
    every = every or (MP4_EVERY if mp4 else RECORD_EVERY)

    def emit(surf):
        """一帧落地：GIF 攒着最后统一编码，mp4 直接喂给 ffmpeg。"""
        src = surf
        if crop:
            cw, ch = crop
            sw, sh = surf.get_size()
            src = surf.subsurface(pygame.Rect((sw - cw) // 2, (sh - ch) // 2, cw, ch))
        if writer is None:
            screens.append(src.copy())
        else:
            writer.stdin.write(
                pygame.image.tostring(pygame.transform.smoothscale(src, MP4_SIZE), "RGB"))

    def capture(_t, game, _mode):
        """每帧回调：正常渲染，每 every 帧存一张。"""
        game._time_accum += dt
        game._handle_audio(dt)
        game._render(dt)
        tick["n"] += 1
        if tick["n"] % every == 0 and t0 <= _t <= t1:
            emit(game.screen)

    # 相位旋钮交给 botrun 摆位（和 audit_phases / --jitter 共用同一个落点函数）。
    # 这里原本自己撸了一份猴补丁，还只敢插值两点巡逻 —— 于是"录像里那条线"
    # 和"统计里那条线"在多点巡逻的关卡上根本不是同一个局面。
    result = bot.run(aware=True, game=g, budget=MAX_SECONDS, on_frame=capture,
                     phase=phase or None)
    w = g.world

    # 结算页多定格一会儿，让观众看清评级和得分。
    # 裁时间窗时不加——那几秒讲的是另一个动作。
    for i in range(0 if clip else int(1.6 * FPS)):
        g._time_accum += dt
        g._render(dt)
        if i % every == 0:
            emit(g.screen)

    if writer is not None:
        writer.stdin.close()
        writer.wait()
    else:
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
    size_mb = Path(out_path).stat().st_size / 1048576 if Path(out_path).exists() else 0
    frames_desc = f"{len(screens)} 帧" if writer is None else "流式编码"
    print(f"已生成 {out_path}（{frames_desc}，{size_mb:.2f} MB，"
          f"结局 {result['state']}，"
          f"用时 {w.elapsed:.1f}s，被发现 {w.spotted} 次，"
          f"击倒 {w.kills} 人，得分 {w.score()}）")
    return result


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--level", type=int, default=0,
                    help="0=哨站 1=环形枢纽 2=柱林中庭 3=配电所 4=查验站")
    ap.add_argument("--out", default=None, help="输出路径（默认按关卡取名）")
    ap.add_argument("--phase", type=float, default=0.0,
                    help="哨兵初始相位 0.0~1.0（沿巡逻线段插值，换个数值换一条走位）")
    ap.add_argument("--clip", default=None,
                    help="只录一段时间窗：『起始秒,时长秒』，例如 --clip 3,9")
    ap.add_argument("--crop", default=None,
                    help="居中裁切后再缩：『宽,高』，例如 --crop 640,480")
    ap.add_argument("--every", type=int, default=None,
                    help="每 N 帧取一张（默认 14≈4.3fps；压小才能拍到石子的飞行）")
    ap.add_argument("--mp4", action="store_true",
                    help="输出 H.264 mp4（960x720 / 30fps）：体积比 GIF 小、发布时不糊")
    a = ap.parse_args()
    clip = None
    if a.clip:
        t0, dur = (float(x) for x in a.clip.split(","))
        clip = (t0, dur)
    crop = None
    if a.crop:
        cw, ch = (int(x) for x in a.crop.split(","))
        crop = (cw, ch)
    record(Path(a.out) if a.out else None, a.level, a.phase, clip, crop, a.every, a.mp4)
