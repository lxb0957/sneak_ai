# itch.io 上架清单

素材已经生成好，这个文件只是"照着填"的字段表。上架要登录你的账号，我替不了这一步。

## 一、先建项目

itch.io → 右上角头像 → **Upload new project**

| 字段 | 填什么 |
|---|---|
| **Title** | `Stealth Protocol` |
| **Project URL** | `stealth-protocol`（最终地址 `你的用户名.itch.io/stealth-protocol`） |
| **Short description**（一句话，会出现在列表和分享卡片里） | `A 2D stealth sandbox where every guard runs an AI you can watch think.` |
| **Classification** | `Game` |
| **Kind of project** | `Downloadable` |
| **Release status** | `Released` |
| **Pricing** | `No payments`（免费）或 `$0 or donate`（允许打赏，推荐这个 —— 不挡路，愿意给的人能给） |

## 二、上传文件

**Uploads → Upload files**，选：

```
C:\Users\刘欣标\WorkBuddy\小游戏\sneak_ai\dist\StealthProtocol-v1.8.11-win64.exe
```

勾上 **This file will be uploaded for Windows**。

> itch 会提示"这是可执行文件，用户下载后会看到警告"。正常现象，所有 Windows 游戏都这样。
> 那个 26 MB 的 exe 就是 Release 页上下载的同一个文件，sha256 已对过：
> `13aac854d419a5e2d9a4cc2f8f83405d27bce97fe12641539470a63115833daf`

如果你的 itch 上传限速，也可以只放链接跳回 GitHub Release —— 但**别这么做**，itch 上"点下载"是转化率最高的一步，跳走就断了。

## 三、图片

| 位置 | 文件 |
|---|---|
| **Cover image**（列表页缩略图，必须有） | `assets/itch/cover.png`（630×500） |
| **Screenshots**（3 张，按顺序） | `assets/itch/screenshot-1.png`（实际画面）<br>`assets/itch/screenshot-2.png`（F1 调试视图）<br>`assets/itch/screenshot-3.png`（第 3 关全图） |

截图可以各加一句说明（itch 支持 caption）：

1. 黄褐色是哨兵的视野锥，撞墙就停
2. 按 F1：每个哨兵的状态、可疑度、听觉半径和最近 9 条决策
3. 五关连着打，每关一个不同的题目

## 四、分类与标签

| 字段 | 填什么 |
|---|---|
| **Genre** | `Action` + `Strategy` |
| **Tags**（最多 10 个，挑最容易被搜到的） | `stealth` `pixel-art` `top-down` `artificial-intelligence` `procedural-generation` `singleplayer` `difficult` `python` `tactical` `robots` |
| **Made with** | `Python`（itch 有 pygame 选项的话选 pygame） |
| **Languages** | `English`、`Chinese`（界面是中文，页面说明有英文） |
| **Input** | Keyboard + Mouse |

## 五、正文

把 `assets/itch/page-description.md` 整个粘进 **Description** 框（itch 的编辑器接受 Markdown，用工具栏右下角那个 `</>` 切到源码模式再粘，格式不会乱）。

## 六、发布前最后看一眼

- [ ] Cover image 已上传（没有封面的项目在列表里是灰块，不会有人点）
- [ ] 至少 3 张截图
- [ ] exe 已上传且勾了 Windows
- [ ] Pricing 选好（免费也建议留 donate 选项）
- [ ] Visibility = **Public**
- [ ] 点 **Save & view page**，自己走一遍下载流程

## 七、上架之后（这些我能做，说一声就行）

1. 把 itch 链接填回 `README.md` 顶部（"不想装 Python？"那段现在只有 GitHub Release 链接）
2. 把 GitHub 仓库的 homepage 从 `releases/latest` 改成 itch 页面（对外落地页放 itch 更合适：有截图、有标签、能被站内搜到）
3. 在 Release 正文里加一行 itch 链接
4. 发一条 devlog（itch 的 devlog 会推给关注者，也是站内曝光的入口）
