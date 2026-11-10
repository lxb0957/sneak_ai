"""从 assets/demo.gif 截一段发社交平台，控制体积。

    python scripts/trim_demo_gif.py --start 24 --seconds 12 --out assets/x-clip.gif

为什么要单独出一份：整段 49.5 秒、6.4 MB，X 虽然允许 15 MB，但超过 5 MB 会被
再压一轮，帧掉得很难看。截出来的片段保留原帧率与调色板，不重编码像素。

默认窗口按 demo.gif 的"被追击"时刻选：红边框警告从 26.5s 起、31–33s 最强，
所以 24–36s 是"发现 → 甩脱"最完整的一段。改 --start 之前先看
`--scan`（打印每帧的红边框强度）确认，别凭时间轴猜。
"""

from __future__ import annotations

import argparse
import os
import sys

from PIL import Image


def _alarm(frame: Image.Image) -> float:
    """边框一圈像素的"红度"均值 —— 被追击时屏幕四周泛红，这个值会从负跳到正。"""
    f = frame.convert("RGB")
    w, h = f.size
    px = f.load()
    total = 0
    count = 0
    for x in range(0, w, 4):
        for y in (2, 4, h - 5, h - 3):
            r, g, b = px[x, y]
            total += r - max(g, b)
            count += 1
    for y in range(0, h, 4):
        for x in (2, w - 3):
            r, g, b = px[x, y]
            total += r - max(g, b)
            count += 1
    return total / count


def scan(path: str) -> None:
    im = Image.open(path)
    fps = _fps(im)
    for i in range(im.n_frames):
        im.seek(i)
        print(f"{i:4d}  {i / fps:6.2f}s  {_alarm(im):7.1f}")


def _fps(im: Image.Image) -> float:
    duration = 0
    for i in range(im.n_frames):
        im.seek(i)
        duration += im.info.get("duration", 0) or 0
    return im.n_frames / (duration / 1000.0) if duration else 6.0


def trim(path: str, out: str, start: float, seconds: float, scale: float) -> None:
    im = Image.open(path)
    fps = _fps(im)
    first = max(0, int(round(start * fps)))
    last = min(im.n_frames, int(round((start + seconds) * fps)))
    if last <= first:
        raise SystemExit(f"窗口为空：start={start}s seconds={seconds}s fps={fps:.2f}")

    frames: list[Image.Image] = []
    durations: list[int] = []
    for i in range(first, last):
        im.seek(i)
        frame = im.convert("RGB")
        if scale != 1.0:
            size = (int(frame.width * scale), int(frame.height * scale))
            frame = frame.resize(size, Image.LANCZOS)
        frames.append(frame)
        durations.append(im.info.get("duration", 0) or int(1000 / fps))

    frames[0].save(
        out,
        save_all=True,
        append_images=frames[1:],
        duration=durations,
        loop=0,
        optimize=True,
    )

    size = os.path.getsize(out)
    print(f"frames {len(frames)}  {frames[0].size[0]}x{frames[0].size[1]}  "
          f"{len(frames) / fps:.1f}s  {size / 1024 / 1024:.2f} MB  -> {out}")
    if size > 5 * 1024 * 1024:
        print("警告：超过 5 MB，X 会二次压缩。减 --seconds 或加 --scale 0.75", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ap = argparse.ArgumentParser(description="截取 demo.gif 的一段用于社交平台发布")
    ap.add_argument("--src", default=os.path.join(here, "assets", "demo.gif"))
    ap.add_argument("--out", default=os.path.join(here, "assets", "x-clip.gif"))
    ap.add_argument("--start", type=float, default=24.0)
    ap.add_argument("--seconds", type=float, default=12.0)
    ap.add_argument("--scale", type=float, default=1.0)
    ap.add_argument("--scan", action="store_true", help="打印每帧红边框强度，不生成文件")
    args = ap.parse_args(argv)

    if args.scan:
        scan(args.src)
        return 0
    trim(args.src, args.out, args.start, args.seconds, args.scale)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
