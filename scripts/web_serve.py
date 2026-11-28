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
  /* 装载页：盖住 pygbag 那个写死 "Downloading..." 的终端，等 canvas 有画面自己退场 */
  #ld { position: fixed; inset: 0; z-index: 2147483000; display: flex; align-items: center;
        justify-content: center; background: #070b12; opacity: 1;
        transition: opacity .45s ease; }
  /* 等着的那十几秒不该是一块纯黑。用一帧实机画面压到 26% 当底，
     从落地页点进来时画面是连着的，不是"跳到一个进度条页面"。
     16 KB，而且和 wasm 不同目录，不抢带宽。 */
  #ld::before { content: ""; position: absolute; inset: 0; pointer-events: none;
                background: url("/ld-bg.jpg") center/cover no-repeat; opacity: .26; }
  #ld::after { content: ""; position: absolute; inset: 0; pointer-events: none;
               background: radial-gradient(ellipse at center,
                           rgba(7,11,18,0) 0%, rgba(7,11,18,.55) 58%, rgba(7,11,18,.9) 100%); }
  #ld .box { width: min(30em, 86vw); text-align: center; position: relative; z-index: 1; }
  #ld h2 { margin: 0 0 .2em; font-size: 1.12em; font-weight: 600; color: #e6edf3; letter-spacing: .02em; }
  #ld .sub { margin: 0 0 1.6em; font-size: .86em; color: #6d7f94; }
  #ld .bar { height: 4px; border-radius: 2px; background: #16202e; overflow: hidden; }
  #ld .bar i { display: block; height: 100%; width: 0; background: #3ea6ff;
               border-radius: 2px; transition: width .3s linear; }
  #ld .meta { margin-top: .8em; font-size: .82em; color: #8fa2b8; font-variant-numeric: tabular-nums; }
  #ld .tip { margin: 2.2em 0 0; min-height: 3.2em; font-size: .9em; line-height: 1.65;
             color: #b6c7da; transition: opacity .26s ease; }
  #ld .tip b { color: #ffd479; font-weight: 600; }
  #ld .foot { margin-top: 1.4em; font-size: .76em; color: #55677c; }
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
<div id="ld"><div class="box">
  <h2>正在装载运行时</h2>
  <p class="sub">第一次打开要把 Python 解释器下到浏览器里</p>
  <div class="bar"><i id="ld-bar"></i></div>
  <p class="meta" id="ld-meta">正在连接…</p>
  <p class="tip" id="ld-tip"></p>
  <p class="foot">之后再打开就是秒开 · 需要键盘和鼠标</p>
</div></div>
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

  // 4) 装载页。pygbag 模板的 #status 写死 "Downloading..."、进度条几乎不动，
  //    用户既不知道总共要下多少，也不知道是在下载还是已经卡死。
  //    这里给真实字节数 + 阶段判断，顺便把等待时间换成有用的话。
  var ld = document.getElementById('ld');
  var ldBar = document.getElementById('ld-bar');
  var ldMeta = document.getElementById('ld-meta');
  var ldTip = document.getElementById('ld-tip');
  var statusEl = document.getElementById('status');

  var TIPS = [
    '按住 <b>Shift</b> 是潜行，脚步没声音。松开就是跑，跑起来整条走廊都听得见。',
    '按 <b>F1</b> 能看见每个哨兵的视野锥和听觉圈，不用猜他在看哪。',
    '被看见了先别跑。找掩体蹲下把视线断掉，他会自己走回去搜。',
    '扔石头能把人引开，但他们会记住石头落在哪，回来时比刚才更警觉。',
    '体力见底就站着别动，几秒回满。冲刺是用来脱身的，不是用来赶路的。',
    '按键没反应，先在画面里点一下 —— 键盘焦点要给到游戏。'
  ];
  var tipIndex = 0;
  if (ldTip) {
    ldTip.innerHTML = TIPS[0];
    setInterval(function () {
      tipIndex = (tipIndex + 1) % TIPS.length;
      ldTip.style.opacity = 0;
      setTimeout(function () { ldTip.innerHTML = TIPS[tipIndex]; ldTip.style.opacity = 1; }, 260);
    }, 4200);
  }

  var TOTAL = 12 * 1048576;        // 线上 gzip 后实测约 11.3 MB，留余量免得进度条提前满了
  var sum = 0, t0 = performance.now(), lastSum = -1, stillSince = performance.now();
  var dismissed = false;

  function leave() {
    if (dismissed || !ld) return;
    dismissed = true;
    ld.style.opacity = 0;
    setTimeout(function () { if (ld.parentNode) ld.remove(); }, 480);
  }

  function paint() {
    var now = performance.now();
    if (sum !== lastSum) { lastSum = sum; stillSince = now; }
    var s = Math.round((now - t0) / 1000);
    var idle = Math.round((now - stillSince) / 1000);
    var mb = sum / 1048576;
    var pct = Math.min(100, Math.round(sum / TOTAL * 100));
    var msg;

    if (ldBar) ldBar.style.width = pct + '%';
    if (mb < 0.05) {
      // 一个字节都没动：要么还在建连，要么这些文件已被落地页预取好、直接走缓存
      msg = idle > 4 ? '运行时已在缓存里，正在编译 (' + s + 's)'
                     : '正在连接… (' + s + 's)';
    } else if (pct >= 100) {
      // 没开 gzip 的服务器（本地预览）会超过预估总量，别显示成 120%
      msg = '已下 ' + mb.toFixed(1) + ' MB，收尾中 (' + s + 's)';
    } else if (idle > 5) {
      msg = '已下 ' + mb.toFixed(1) + ' MB，还在等 (' + s + 's)';
    } else {
      msg = '已下 ' + mb.toFixed(1) + ' MB / 约 11 MB · ' + pct + '% · ' + s + 's';
    }
    if (ldMeta) ldMeta.textContent = msg;
    if (statusEl) statusEl.textContent = msg;     // 装载页万一没挂上，还有这行兜底
  }

  // 画面真的出来了才退场：pygbag 的 canvas 一开始就存在，但 width 是 1。
  // 再多留 0.9 秒 —— width 刚变时主菜单往往还没画完，慢机器上会闪一下黑屏。
  var watch = setInterval(function () {
    var c = document.querySelector('canvas');
    if (c && c.width > 1) { clearInterval(watch); setTimeout(leave, 900); }
  }, 400);
  setTimeout(function () { clearInterval(watch); leave(); }, 120000);   // 兜底，别永久挡住

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
