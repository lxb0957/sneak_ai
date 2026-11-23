# 分发与发布

这份文档解决一个问题：**怎么让全国各地的人真的能打开、下载、玩到。**

不是"怎么打包"（那是 `build_exe.py`），是"打包之后怎么送到人手上"。

---

## 零、当前线上入口（2026-11-23 已上线）

**对外只发这一个链接**：

```
https://stealth-protocol.app.workbuddy.host/?sharecode=777DHSP3EJ
```

一页收口三件事，不需要再给第二个地址：

| 路径 | 内容 | 实现 |
|---|---|---|
| `/` | 落地页（首屏、AI 说明、按键表、配置要求） | `web/landing/index.html` |
| `/play/` | 浏览器直接玩（pygbag / CPython wasm） | `web_site/` 全量搬进 `play/`，`cdn/` 提到站点根 |
| `/download/stealth-protocol-win64` | Windows 便携包 26 MB | `dist/release/*.zip` 改名去掉 `.zip` |

产物目录：`dist/launch/`（51 MB 打包上传）。重新发布：

```bash
python scripts/build_web.py --verify          # 重建网页版 → web_site/
python build_exe.py                           # 重建便携包（若有改动）→ dist/release/
# 组装 dist/launch/：landing + play/(=web_site) + cdn/(从 play/ 提出来) + download/
# 然后用发布工具重新部署 dist/launch/
```

**为什么下载文件没有 `.zip` 后缀**：托管网关前置了腾讯云 WAF，`.zip` / `.rar` / `.7z`
（含 `.ZIP` 大写、`x.zip.apk` 这种复合名）一律返回 403 WAF 拦截页，`.apk` / `.pkg` /
`.exe` / 无扩展名全部放行。所以文件挂在无扩展名路径上，靠 `<a download="…zip">` 让浏览器
存成正常文件名（Chrome / Edge / Firefox 实测保存名正确）。探测方法：请求一个**不存在**的
`/probe/x.<ext>`，404 = 放行，403 = 被 WAF 拦。

**分享码必须跟着导航走**：网关认的是「导航 URL 上的 `?sharecode=`」，只靠它种下的
`__wb_rsc` cookie **不够** —— 页面里普通的 `fetch(..., {credentials:'same-origin'})` 能过，
但 wasm 运行时发的那些请求（不带 `sec-fetch-*`）会被 403，表现是从落地页点进 `/play/`
之后永远停在 1×1 的空 canvas 上。所以落地页有一段脚本把首屏查询串原样带到游戏页：

```js
if (a && location.search) a.href = 'play/' + location.search;   // → /play/?sharecode=…
```

同理，对外发的链接**必须带 `?sharecode=`**，裸域名会 403。

---

## 一、先认清三个坑

| 坑 | 表现 | 为什么 |
|---|---|---|
| GitHub 下载慢/断 | `objects.githubusercontent.com` 常年几十 KB/s 或连不上，26 MB 下不完 | 这是 GitHub 的资产 CDN，国内没有落地节点 |
| 自定义域名要备案 | 绑域名到国内服务器/CDN，未备案直接被拦 | `ICP 备案` 是中国大陆境内的强制要求，个人也能备，但要先有国内服务器 |
| 没人知道 | 发了也 0 下载 | 技术通道和传播是两件事，前者修好不等于后者 |

**关键事实**：腾讯云 COS / 阿里云 OSS 的**默认域名**（`xxx.cos.ap-guangzhou.myqcloud.com`）
不需要备案，只要账号实名。但它对 HTML 强制 `Content-Disposition: attachment`，
所以**只能拿来发下载，不能托管网页版**。这条差别决定了下面两条路分开走。

---

## 二、桌面版：发到 COS

### 1. 一次性配置（约 10 分钟）

1. 注册腾讯云并实名：<https://cloud.tencent.com/>
2. 开通对象存储 COS：<https://console.cloud.tencent.com/cos>
3. **创建存储桶**
   - 名称随便，例如 `sneak-release`
   - 地域选**离你主要受众近的**：华东（上海）/ 华南（广州）
   - 访问权限选 **「公有读私有写」**
   - 记下完整的桶名，形如 `sneak-release-1250000000`（**必须带 APPID 后缀**）
4. **拿密钥**：<https://console.cloud.tencent.com/cam/capi>
   - 建一个子用户，只给 COS 权限，别用主账号密钥
   - 拿到 `SecretId` / `SecretKey`
5. 写配置文件（比每次敲命令行安全）：

```json
// ~/.sneak_cos.json   —— Windows 是 C:\Users\<你>\.sneak_cos.json
{
  "secret_id": "AKIDxxxxxxxxxxxxxxxx",
  "secret_key": "xxxxxxxxxxxxxxxxxxxxxxxxxx",
  "bucket": "sneak-release-1250000000",
  "region": "ap-guangzhou"
}
```

> 这个文件**不要提交进仓库**（已在 `.gitignore` 里）。

### 2. 每次发版

```bash
python scripts/cos_upload.py --check                 # 凭据通不通
python scripts/cos_upload.py                         # 上传 dist/release/*.zip 并验证直链
```

输出末尾会打印直链，形如：

```
[ok] 200 26.3MB  https://sneak-release-1250000000.cos.ap-guangzhou.myqcloud.com/sneak_ai/StealthProtocol-v1.8.14-win64.zip
```

**把这条链接填进 `web/landing/index.html` 的下载按钮**，以及各平台简介。

### 3. 成本

存储 0.118 元/GB/月，外网下行流量 0.5 元/GB。
26 MB 的包意味着**一次下载约 1.3 分钱**，1000 次下载约 13 元。

设置了 CDN 加速会更快，但要绑自定义域名（然后就绕回备案了）。
纯对象存储默认域名在国内已经够用，先别急着上 CDN。

---

## 三、网页版：换一个不带 workbuddy 的地址

网页版是**纯静态**：24 MB、没有后端、没有数据库，不依赖任何一家服务的运行时。
换托管只是把文件夹搬过去，代码一行不用改。两件事必须先记住：

1. **必须部署在站点根路径**。`play/index.html` 里是 `/cdn/0.9.3/...` 绝对路径，pygbag 运行时
   也是用 `location.origin + "/cdn/"` 拼索引地址。放到 `https://host/sneak_ai/` 这种子路径会 404。
2. **不需要任何特殊响应头**。COOP/COEP 都不用（单线程 wasm 就能跑），实测 2.9 s 出画面。

已经备好两个可直接上传的包：

| 包 | 大小 | 内容 | 用途 |
|---|---|---|---|
| `dist/launch_web.zip` | 11.8 MB（展开 23.7 MB） | 落地页 + 网页版，**不含 exe**，下载按钮指向 GitHub Releases | 有单文件上限的托管（Cloudflare Pages 25 MB/文件） |
| `dist/launch.zip` | 38.1 MB（展开 49.9 MB） | 上面 + 26.3 MB 便携包 | 对象存储 / Netlify 这类不限单文件大小的 |

```bash
python scripts/build_launch.py --web-only --zip    # → dist/launch_web.zip
python scripts/build_launch.py --zip               # → dist/launch.zip
```

| 方案 | 备案 | 国内可达 | URL 形态 | 怎么弄 |
|---|---|---|---|---|
| **Cloudflare Pages** | 不需要 | 一般能开，偶尔慢 | `xxx.pages.dev` | 登录 → Workers & Pages → Create → Pages → Upload assets → 拖 `launch_web.zip`。免费，流量不限 |
| **腾讯云 COS 静态网站** | 不需要（默认域名） | 快 | `<bucket>.cos-website.ap-guangzhou.myqcloud.com` | 桶里开「静态网站」功能，把解压后的文件传上去，索引文档填 `index.html`。国内最稳的免备案方案 |
| **Netlify** | 不需要 | 波动大 | `xxx.netlify.app` | 拖 `launch.zip` 到 app.netlify.com/drop |
| **GitHub Pages（用户主页）** | 不需要 | 不稳（DNS 干扰） | `lxb0957.github.io` | 必须建**用户主页仓库** `lxb0957.github.io` 才在根路径；建普通项目仓库会落到 `/sneak_ai/` 子路径，直接 404 |
| **Vercel** | 不需要 | 经常不通 | `xxx.vercel.app` | 别用，`vercel.app` 域名在国内被墙的比例很高 |

**结论**：想让全国的人都能稳定打开 → 走腾讯云 COS 静态网站（默认域名、免备案、国内节点）。
只是发给朋友试玩、不追求国内速度 → Cloudflare Pages 最快，拖个 zip 就有链接。

---

## 四、传播：真正的瓶颈在这

技术通道修完，下载量还是 0，是常态。国内独立游戏起量的实际顺序：

1. **B 站** —— 实机视频 > 图文。文案在 `assets/posts/04-bilibili.txt`，
   视频用 `python scripts/make_bili_video.py` 生成
2. **小红书** —— 截图 + 一句话钩子（"我用 Python 写了个潜行游戏"），
   不需要视频，门槛最低
3. **小黑盒 / NGA / 游民星空论坛** —— PC 玩家聚集地，接受独立小游戏
4. **抖音 / 快手** —— 15 秒实机，切片就行
5. **V2EX / 少数派** —— 技术向，讲 AI 怎么写的，不是讲游戏

各平台的帖子记得都指向**同一个落地页**，别一个平台给一个链接，
换下载源时才知道往哪改。

---

## 五、每次发版的检查清单

- [ ] `python -m pytest -q` 与关键脚本测试全绿
- [ ] 版本号 8 处一致（`scripts/test_w40_version_sync.py` 会查）
- [ ] `python build_exe.py` 出 exe，`--windowed` 打包后双击能开
- [ ] 打便携包 zip（exe + `使用说明.txt`）
- [ ] `python scripts/cos_upload.py` 上传并确认直链 200
- [ ] `web/landing/index.html` 的下载按钮指向新链接
- [ ] `web/README.md` 里的版本号同步
- [ ] GitHub Release 也传一份（海外用户 + 存档）

---

## 附：几个踩过的坑

**`.tar.gz` 会被某些网关按扩展名拦 403。** 换扩展名（`.pkg`）即可，内容不变。

**网页版的运行时请求不带 cookie。** pygbag 的 `cross_file.fetch` 用
`credentials: 'omit'`，如果部署平台有鉴权层（分享码之类），这些请求会 403。
处理方式见 `web/README.md` 的"分享码门槛"一节。

**wheel 的 URL 结尾带 `#`。** 往末尾拼查询串会掉进片段里，服务器看不见。
查询串必须插在 `#` 之前。
