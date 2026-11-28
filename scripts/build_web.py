"""一条命令产出可上线的网页版：构建 → 拉齐运行时 → 打补丁 → 导出静态站。

    python scripts/build_web.py --proxy http://127.0.0.1:7897
    python scripts/build_web.py --proxy ... --verify     # 顺带用无头浏览器跑一遍

产物在 `web/build/web/`，24 MB 左右，丢到任何静态空间（GitHub Pages / Netlify /
对象存储）都能直接玩，运行时不依赖 GitHub Pages。

为什么要"拉齐运行时"：pygbag 的产物只是一层壳，CPython wasm（13 MB）、
pygame-ce 的 wasm 轮子、xterm 等全都得在**浏览器打开的那一刻**从
`pygame-web.github.io` 现拉。国内直连常年 80 KB/s 且会断，这就是
"页面一直停在下载 main.wasm" 的根因。镜像到本地后一次拉好、永久离线。
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
sys.path.insert(0, str(Path(__file__).resolve().parent))

import web_serve as ws  # noqa: E402

# 浏览器打开页面时会顺序请求的东西，缺一个就卡住
RUNTIME = [
    "pythons.js",
    "vtx.js",
    "empty.html",
    "cpythonrc.py",
    "vt/xterm.js",
    "vt/xterm.css",
    "vt/xterm-addon-image.js",
    "cpython312/main.js",
    "cpython312/main.data",
    "cpython312/main.wasm",
    "index-0.9.3-cp312.json",
]


def build() -> int:
    cmd = [
        sys.executable,
        "-m",
        "pygbag",
        "--build",
        "--ume_block",
        "0",
        "--width",
        "1280",
        "--height",
        "960",
        "--title",
        "Sneak AI",
        str(WEB),
    ]
    print("== 1/4 pygbag 构建", flush=True)
    r = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        print(r.stdout[-3000:])
        print(r.stderr[-3000:])
    return r.returncode


def warm(proxy: str) -> int:
    """把运行时拉进镜像。轮子文件名从索引 json 里现读，不写死版本。"""
    print("== 2/4 拉齐运行时", flush=True)
    ws._proxy = proxy or None
    ws.MIRROR.mkdir(parents=True, exist_ok=True)

    missing = [rel for rel in RUNTIME if ws.fetch_remote(rel) is None]
    if missing:
        print("  缺文件:", ", ".join(missing))
        return 1

    idx_path = ws.MIRROR / "index-0.9.3-cp312.json"
    try:
        table = json.loads(idx_path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"  索引 json 读不了: {e}")
        return 1

    wheel = table.get("pygame", "")
    if not wheel:
        print("  索引里没有 pygame 条目")
        return 1
    wheel = wheel.replace("<abi>", "cp312").replace("<api>", "wasm32_bi_emscripten")
    if ws.fetch_remote(wheel) is None:
        return 1

    print(f"  镜像共 {sum(1 for _ in ws.MIRROR.rglob('*') if _.is_file())} 个文件", flush=True)
    return 0


def patch() -> int:
    print("== 3/4 打自给补丁", flush=True)
    return subprocess.call([sys.executable, str(ROOT / "scripts" / "web_patch_runtime.py")], cwd=str(ROOT))


def export() -> int:
    print("== 4/4 导出静态站", flush=True)
    rc = ws.offline_export()
    if rc == 0:
        # 装载页底图。它和落地页素材放在一起，但页面从 /play/ 引用 /ld-bg.jpg，
        # 所以得在站点根也有一份 —— 手工复制漏一次，线上装载页就退回纯黑。
        bg = WEB / "landing" / "ld-bg.jpg"
        if bg.exists():
            shutil.copy2(bg, ws.OUT / "ld-bg.jpg")
    return rc


def verify() -> int:
    import socket
    import threading
    from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

    class Quiet(SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(ws.OUT), **kw)

        def log_message(self, *a):
            pass

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    srv = ThreadingHTTPServer(("127.0.0.1", port), Quiet)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print(f"== 验证：朴素静态服务器 http://127.0.0.1:{port}/", flush=True)
    rc = subprocess.call(
        [sys.executable, str(ROOT / "scripts" / "web_smoke.py"), "--url", f"http://127.0.0.1:{port}/"],
        cwd=str(ROOT),
    )
    srv.shutdown()
    return rc


def sync_source() -> int:
    """把根目录的 `sneak_ai/` 同步进 `web/sneak_ai/`，**打包前必做**。

    pygbag 打包的是 `web/` 这个目录（入口 `web/main.py` 在里面），所以
    `web/sneak_ai/` 是一份副本。以前靠手工复制，后果很阴：

        改了源码 → 跑构建 → 构建成功、冒烟全绿 → 线上还是旧文案

    因为冒烟只验"画面非空、能进关卡"，不验字。2026-11-24 改界面文案时
    就是这么被骗过一轮，截图才发现菜单还是老的。所以同步塞进构建流程，
    并且把"改了几份文件"打出来，让每次构建都自证。
    """
    src = ROOT / "sneak_ai"
    dst = WEB / "sneak_ai"
    print("== 0/4 同步源码到打包目录", flush=True)
    changed = 0
    for item in src.rglob("*"):
        if "__pycache__" in item.parts:
            continue
        target = dst / item.relative_to(src)
        if item.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        if target.exists() and target.read_bytes() == item.read_bytes():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, target)
        changed += 1
    print(f"  {src} -> {dst}（更新 {changed} 个文件）", flush=True)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--proxy", default="", help="拉运行时用的代理，例如 http://127.0.0.1:7897")
    ap.add_argument("--verify", action="store_true", help="导出后用无头浏览器真跑一遍")
    ap.add_argument("--skip-build", action="store_true")
    ap.add_argument("--skip-warm", action="store_true")
    args = ap.parse_args()

    if sync_source() != 0:
        return 1
    if not args.skip_build and build() != 0:
        return 1
    if not args.skip_warm and warm(args.proxy) != 0:
        return 1
    if patch() != 0:
        return 1
    if export() != 0:
        return 1
    if args.verify:
        return verify()
    print(f"\n完成：{ws.OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
