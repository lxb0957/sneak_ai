"""自托管网页版：静态服务 + 运行时镜像。

为什么不用 `python -m pygbag` 直接跑：
它内置的测试服务器收到 `/cdn/xxx` 时会**同步**去 pygame-web.github.io
拉文件（单线程，拉 13 MB 的 main.wasm 期间整站阻塞）。国内直连约 80 KB/s，
表现就是"页面永远停在下载 main.wasm"，而且这个依赖上线后会一直都在。

这里把运行时**镜像到本地**：第一次缺什么就用代理拉什么，落到
`web/build/web-cache/mirror/`，之后完全离线。index.html 里的
`https://pygame-web.github.io` 前缀会被改写成 `/cdn/`，让浏览器来问本地。

用法：
    python scripts/web_serve.py --port 8123 [--proxy http://127.0.0.1:7897]

上线前打包成纯静态目录：
    python scripts/web_serve.py --offline-export
（把镜像拷进 build/web/cdn 并把 index.html 改成相对路径）
"""

from __future__ import annotations

import argparse
import mimetypes
import re
import shutil
import sys
import threading
import urllib.error
import urllib.request
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "web" / "build" / "web"
MIRROR = ROOT / "web" / "build" / "web-cache" / "mirror"
# 导出目录。刻意不叫 build/ 或 dist/：那是"构建产物"的通用名字，
# 有些上传/打包工具会按名字直接跳过。
OUT = ROOT / "web_site"
VER = "0.9.3"
CDN_URL = f"https://pygame-web.github.io/cdn/{VER}/"
CDN_ROOT = "https://pygame-web.github.io/cdn/"
CDN_HOST = "https://pygame-web.github.io"

mimetypes.add_type("application/wasm", ".wasm")
mimetypes.add_type("text/javascript", ".js")

_lock = threading.Lock()
_proxy: str | None = None


def _candidates(rel: str) -> list[str]:
    """同一个资源在 CDN 上可能挂在带版本号或不带版本号的路径下，都试。"""
    bare = rel[len(VER) + 1 :] if rel.startswith(VER + "/") else rel
    out = []
    for r in (rel, bare):
        for base in (CDN_URL, CDN_ROOT):
            u = base + r
            if u not in out:
                out.append(u)
    return out


def fetch_remote(rel: str) -> Path | None:
    """拉一个运行时文件到镜像目录，返回本地路径。"""
    bare = rel[len(VER) + 1 :] if rel.startswith(VER + "/") else rel
    dest = MIRROR / bare
    if dest.is_file() and dest.stat().st_size > 0:
        return dest

    handlers = []
    if _proxy:
        handlers.append(urllib.request.ProxyHandler({"http": _proxy, "https": _proxy}))
    opener = urllib.request.build_opener(*handlers)

    data = None
    for url in _candidates(rel):
        try:
            with opener.open(url, timeout=180) as r:
                data = r.read()
            print(f"  [cdn] 拉取 {bare} ({len(data) / 1024:.0f} KB) <- {url}", flush=True)
            break
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
            last = e
    if data is None:
        print(f"  [cdn] 失败 {bare}: {last}", flush=True)
        return None

    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    tmp.write_bytes(data)
    tmp.replace(dest)
    return dest


def rewrite_html(text: str) -> str:
    """把 index.html 里的 CDN 绝对地址换成本地地址。

    用**绝对路径** `/cdn/0.9.3/` 而不是相对路径，有两个硬原因：
    1. `vtx.js` 里 `import(cdn + "../vt/xterm.js")` 是**模块说明符**，
       相对路径（`cdn/0.9.3/../vt/xterm.js`）不合法，会直接抛
       `Failed to resolve module specifier`；以 `/` 开头才被当成 URL。
    2. 必须保留版本目录：`../` 规范化后会落到 `/cdn/vt/`，
       导出时那批小文件额外复制了一份到 `cdn/` 根下。
    """
    prefix = f"/cdn/{VER}/"
    text = text.replace(CDN_URL, prefix)
    text = text.replace(CDN_HOST + "/cdn/" + VER + "/", prefix)
    text = text.replace(CDN_HOST, "")
    # 重复导出时上一次留下的相对前缀也要纠正成绝对路径
    text = re.sub(r"(?<![/\w])" + re.escape(f"cdn/{VER}/"), prefix, text)
    text = text.replace(prefix + "/", prefix)  # 原模板里有无意的双斜杠
    return inject_web_extras(text)


# 社交分享卡片：别人把链接发出去时，光秃秃一条 URL 没人点
HEAD_EXTRA = """<meta property="og:type" content="website">
    <meta property="og:title" content="潜行协议 Stealth Protocol">
    <meta property="og:description" content="潜行 + AI 感知的浏览器小游戏：哨兵的视野锥和听觉圈都是真的，5 关，打开就能玩。">
    <meta property="og:image" content="https://lxb0957.github.io/og.png">
    <meta name="twitter:card" content="summary_large_image">
    </head>"""

WEB_MARK = "<!-- [selfhost] 网页版体验补丁 -->"

BODY_EXTRA = """<!-- [selfhost] 网页版体验补丁 -->
<style>
  /* pygbag 用 JS 内联把 body 背景设成 #7f7f7f，内联样式只能用 !important 压过去 */
  html, body { margin: 0; background: #070b12 !important; color: #e6edf3; }
  #canvas:focus { outline: none; }
  #tp { display: none; }
  @media (hover: none) and (pointer: coarse) {
    #tp { display: flex; position: fixed; inset: 0; z-index: 2147483647;
          background: #0b1220; color: #e6edf3; align-items: center; justify-content: center;
          font: 16px/1.65 system-ui, -apple-system, "Segoe UI", "Microsoft YaHei", sans-serif; }
    #tp .box { max-width: 30em; padding: 0 28px; text-align: center; }
    #tp h2 { font-size: 1.25em; margin: 0 0 .6em; }
    #tp p { margin: .5em 0; color: #9fb0c4; }
    #tp button { margin-top: 1.2em; padding: 10px 22px; border: 1px solid #2f4257;
                 border-radius: 8px; background: #16202e; color: #cfe0f0; font-size: .95em; cursor: pointer; }
  }
</style>
<div id="tp"><div class="box">
  <h2>这个游戏需要键盘和鼠标</h2>
  <p>当前设备是触屏，没有 WASD 移动和鼠标瞄准，暂时玩不了。</p>
  <p>请换电脑用 Chrome / Edge 打开同一个链接；想立刻玩，也可以下载 Windows 桌面版。</p>
  <button id="tp-go">还是想试试</button>
</div></div>
<script>
/* [selfhost] 三件事：抢回被浏览器吃掉的按键、锁住键盘焦点、给触屏设备一个交代 */
(function () {
  // 1) 游戏用到的键会被浏览器默认行为吃掉（Tab 跳地址栏、Space 滚页面、F1 开帮助）。
  //    只 preventDefault，不 stopPropagation —— 一旦阻断冒泡，canvas 上的 pygame 就收不到了。
  var blocked = { Tab: 1, Space: 1, F1: 1, ArrowUp: 1, ArrowDown: 1, ArrowLeft: 1, ArrowRight: 1 };
  window.addEventListener('keydown', function (e) {
    if (e.ctrlKey || e.metaKey || e.altKey) return;   // 浏览器/系统级组合键一律不碰
    if (blocked[e.code] || blocked[e.key]) e.preventDefault();
  }, true);

  // 2) 焦点必须留在 canvas 上，否则 WASD 会被页面别处接走
  var cv = document.getElementById('canvas');
  function grab() {
    if (cv && document.activeElement !== cv) {
      try { cv.focus({ preventScroll: true }); } catch (x) { cv.focus(); }
    }
  }
  grab();
  document.addEventListener('click', grab);
  setInterval(grab, 1200);

  // 3) 触屏设备：别让人对着空白页猜为什么没反应
  var tp = document.getElementById('tp');
  var go = document.getElementById('tp-go');
  if (go) go.addEventListener('click', function () { tp.style.display = 'none'; grab(); });

  // 4) pygbag 模板的 #status 写死 "Downloading..."，进度条几乎不动。
  //    整包约 30 MB 时用户完全看不出是在下载还是卡死。
  var el = document.getElementById('status');
  if (!el) return;
  var sum = 0, t0 = performance.now(), lastSum = -1, stillSince = performance.now();
  function paint() {
    var now = performance.now();
    if (sum !== lastSum) { lastSum = sum; stillSince = now; }
    var s = Math.round((now - t0) / 1000);
    var idle = Math.round((now - stillSince) / 1000);
    var mb = sum / 1048576;
    var head = (mb > 0.05) ? 'Downloading... ' + mb.toFixed(1) + ' MB' : 'Loading...';
    // 字节数不再增长 = 已进入解压 / wasm 编译阶段（纯 CPU，没有网络事件）
    el.textContent = head + (idle > 4 ? '，正在解压与编译 (' + s + 's)' : '  (' + s + 's)');
  }
  if (window.PerformanceObserver) {
    try {
      new PerformanceObserver(function (list) {
        list.getEntries().forEach(function (e) {
          sum += e.transferSize || e.encodedBodySize || 0;
        });
        paint();
      }).observe({ type: 'resource', buffered: true });
    } catch (e) {}
  }
  setInterval(paint, 300);
})();
</script>
</body>"""


def inject_web_extras(text: str) -> str:
    """往导出的 index.html 里补三样东西：分享卡片 meta、按键/焦点补丁、加载进度。

    幂等：靠 WEB_MARK 判断，重复导出不会叠加。
    """
    if WEB_MARK in text:
        return text
    if "</head>" in text:
        text = text.replace("</head>", HEAD_EXTRA, 1)
    if "</body>" in text:
        text = text.replace("</body>", BODY_EXTRA, 1)
    else:
        text += BODY_EXTRA
    return text


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(BUILD), **kw)

    def end_headers(self):
        # pygbag 的 wasm 需要跨源隔离（SharedArrayBuffer）。COEP 一旦开启，
        # 任何跨源子资源都必须自带 CORP —— 所以页面和 /cdn/ 必须**同源**。
        # 这里所有资源都从本服务器出，天然同源。
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        self.send_header("Cross-Origin-Embedder-Policy", "require-corp")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        super().end_headers()

    def log_message(self, fmt, *args):  # 安静点
        if "/cdn/" not in self.path:
            print(f"  [http] {self.path}", flush=True)

    def do_GET(self):
        path = self.path.split("?", 1)[0].lstrip("/")

        if path.startswith("cdn/"):
            rel = path[len("cdn/") :]
            with _lock:
                f = fetch_remote(rel)
            if f is None:
                self.send_error(404, "runtime file unavailable")
                return
            body = f.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", mimetypes.guess_type(str(f))[0] or "application/octet-stream")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "max-age=86400")
            self.end_headers()
            self.wfile.write(body)
            return

        target = BUILD / path
        if path.endswith(".html") or path in ("", "/"):
            f = target if target.is_file() else BUILD / "index.html"
            if f.is_file():
                text = rewrite_html(f.read_text(encoding="utf-8"))
                body = text.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return

        # 兜底：`cdn/0.9.3/../vt/` 这类拼接万一规范化到站点根，也在本地补齐
        if not (BUILD / path).is_file() and path and not path.startswith("cdn/"):
            if Path(path).suffix in (".js", ".wasm", ".css", ".py", ".data"):
                with _lock:
                    f = fetch_remote(VER + "/" + path)
                if f is not None:
                    body = f.read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type", mimetypes.guess_type(str(f))[0] or "application/octet-stream")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return

        return super().do_GET()

    def do_HEAD(self):
        # playwright 之类会先 HEAD 探活，别让它把镜像逻辑跑一遍
        return super().do_HEAD()


def offline_export() -> int:
    """把 pygbag 产物 + 运行时镜像合成一个可直接上传的静态目录（默认 web_site/）。"""
    if not MIRROR.is_dir():
        print("镜像还是空的，先用服务器跑一遍再导出")
        return 1
    if not (BUILD / "index.html").is_file():
        print(f"还没构建：{BUILD}")
        return 2

    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)

    # 1) pygbag 的壳：index.html / web.tar.gz / web.apk / favicon
    for f in BUILD.iterdir():
        if f.name in ("cdn", "port.txt"):
            continue
        if f.is_file():
            shutil.copy2(f, OUT / f.name)

    # 2) 运行时镜像。页面请求 cdn/0.9.3/xxx；而 `cdn + "../vt/"` 会被规范化到
    #    cdn/vt/，所以小文件（vt/、vtx.js）要额外复制一份到 cdn/ 根下。
    dst = OUT / "cdn"
    shutil.copytree(MIRROR, dst / VER, ignore=shutil.ignore_patterns("*.orig.js"))
    extra = []
    for entry in MIRROR.iterdir():
        if entry.name.endswith(".orig.js"):  # 补丁用的原版备份，不上线
            continue
        size = sum(f.stat().st_size for f in entry.rglob("*") if f.is_file()) if entry.is_dir() else entry.stat().st_size
        if size < 1024 * 1024:
            target = dst / entry.name
            if entry.is_dir():
                shutil.copytree(entry, target)
            else:
                shutil.copy2(entry, target)
            extra.append(entry.name)

    # 3) 只改导出副本的 index.html，build/web 里的原始模板保持原样
    idx = OUT / "index.html"
    idx.write_text(rewrite_html(idx.read_text(encoding="utf-8")), encoding="utf-8")

    # 4) 另存一份 .pkg：部署平台的网关按扩展名拦 .tar.gz（403），
    #    pythons.js 已补丁成改请求 .pkg。内容完全一样。
    tar = OUT / "web.tar.gz"
    if tar.is_file():
        shutil.copy2(tar, OUT / "web.pkg")

    # 5) 分享卡片图：og:image 指的就是这个文件名
    og = ROOT / "web" / "og.png"
    if og.is_file():
        shutil.copy2(og, OUT / "og.png")
    else:
        print("  提示：web/og.png 不存在，分享卡片会没有配图（跑 scripts/make_og.py 生成）")

    n = sum(1 for _ in dst.rglob("*") if _.is_file())
    size = sum(_.stat().st_size for _ in dst.rglob("*") if _.is_file())
    total = sum(f.stat().st_size for f in OUT.rglob("*") if f.is_file())
    print(f"已导出 {n} 个运行时文件（{size / 1024 / 1024:.1f} MB）到 {dst}")
    print(f"  额外复制到 cdn/ 根：{', '.join(extra) or '无'}")
    print(f"  整站 {total / 1024 / 1024:.1f} MB -> {OUT}")
    print(f"index.html 已指向 /cdn/{VER}/，整个 {OUT} 就是可上传的静态站")
    print("注意：路径以 / 开头，要部署在站点根目录（子路径部署需前置一层 base）")
    return 0


def main() -> int:
    global _proxy
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9123)
    ap.add_argument("--proxy", default="")
    ap.add_argument("--offline-export", action="store_true")
    args = ap.parse_args()

    if args.offline_export:
        return offline_export()

    _proxy = args.proxy or None
    if str(args.port).startswith("8"):
        print(
            f"警告：端口 {args.port} 以 8 开头，pygbag 的 PEP0723 会据此判定"
            "为开发模式并把包索引硬改成 http://localhost:8000/cdn/（见 pep0723.py:205），"
            "结果必然是 CORS 拦截。换个不以 8 开头的端口。",
            flush=True,
        )
    if not BUILD.is_dir():
        print(f"还没构建：{BUILD}")
        return 2
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"http://127.0.0.1:{args.port}/   镜像目录 {MIRROR}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
