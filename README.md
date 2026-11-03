# 📁 文件浏览器 (qwenpaw-file-browser) v0.2.5

QwenPaw 文件浏览器插件：在 QwenPaw 界面里分层级浏览、查看、上传、下载文件与目录，自动识别运行环境（qwenpaw-agentscope-platform 平台 / 本地部署），平台与本地通用。采用 [Apache License 2.0](LICENSE) 许可协议发布。

## 功能（v0.2.5）

- 📂 **分层级浏览目录**：图标 / 名称 / 大小 / 修改时间 / 绝对路径；
  **表头点击排序**（名称 / 大小 / 修改时间，再点切换升/降序 ▲▼，目录始终优先）
- 🧭 **刷新保持当前位置**：当前打开的目录记入 localStorage，刷新页面后自动
  恢复到上次打开的位置（保存路径失效时自动清理并回退）
- 🧭 **路径栏**：左侧「工作区 ▾」下拉可在「工作区 / 根目录」之间切换，后面是各级
  可点击路径；右侧「复制」「收藏」两个按钮
- ⭐ **收藏**：工具栏「⭐ 收藏」面板列出**可访问根目录**（WORKING_DIR / 智能体工作区 /
  系统路径 / NAS）与**我的收藏**条目；路径行「收藏」可将当前路径加入收藏
  （localStorage 持久化，右侧 ✕ 可移除）
- ⬆️ **上传文件**：多选上传到当前目录，文件名冲突自动重命名为 `name (1).ext`，流式写入
- 📥 **拖拽上传**：把文件/文件夹直接拖入浏览器窗口即可上传到当前目录，悬停显示高亮提示
- 📁 **上传文件夹**：一键选择整个文件夹上传，自动保留目录结构（多层子目录），同名目录自动合并、文件冲突自动重命名
- 🌐 **访问范围自动适配**：
  - qwenpaw-agentscope-platform 平台（WORKING_DIR 位于 `/run/csi/mount-root/nas` 之下）
    默认「平台模式」：可访问所有支持访问的路径（NAS 持久层 / 容器本地盘 `/tmp` `/home`
    `/root` `/workspace` / 系统盘 `/app` 等只读区域），遵循操作系统权限
  - 本地部署默认「工作区模式」：仅访问 QwenPaw 根目录（越界返回 400）
  - 访问范围由环境自动判定；界面不再提供模式切换按钮（需要时仍可调用
    `POST /api/qwenpaw-file-browser/mode` 手动覆盖）
- 🤖 **收藏面板含各智能体工作区**：工作区模式下自动扫描 WORKING_DIR 下的
  `workspaces/` 目录，将每个智能体的工作区以「🤖 <agent_id>（工作区）」加入
  收藏面板，一键直达任意智能体的工作区（平台模式下同样列出）
- 📁 **文件夹操作**：新建文件夹、重命名（文件/文件夹）、删除（目录递归删除带二次确认）；
  文件夹行的「下载」= 打包下载该文件夹（zip）
- 📋 **复制路径**：路径行「复制」按钮复制当前目录；每个文件/文件夹操作区也提供
  「复制路径」按钮（复制绝对路径到剪贴板，干净无附加文字）
- ✅ **批量操作**：多选（勾选/全选）→ 批量打包下载（zip，目录递归收集）或批量删除；
  多选模式下自动隐藏上传类按钮（避免误操作）
- 📄 **预览渲染（不限大小）**：全量读取，无 256KB 截断 / 10MB 硬限制
  - **Markdown**（`.md` / `.markdown`）渲染：标题 / 列表 / 任务列表 / 表格 / 引用 /
    代码块（带语法高亮）/ 链接 / 图片等，GitHub Dark 风格
  - **JSON / JSONC**：自动格式化（缩进 2 空格）+ 语法高亮
  - **场景/配置文件**（`.yaml` / `.yml` / `.toml` / `.ini` / `.conf` / `.cfg` /
    `.env` / `.properties` / `.xml`）：语法高亮渲染
  - **常见代码文件**（`.py` / `.js` / `.ts` / `.sh` / `.go` / `.rs` / `.c` / `.cpp` 等）：语法高亮
  - 渲染策略：运行时优先复用宿主全局的 `marked` / `hljs` / `Prism`（若存在），
    否则内置轻量渲染器（零外部依赖、离线可用），外部库输出经 HTML 清洗
  - 二进制文件提示下载
- ⬇️ **下载**：任意文件（二进制安全，附件方式）
- 📊 **传输进度**：上传 / 下载（含批量打包）显示左下角浮层进度条——百分比、已传/总字节、实时速度；服务器打包/排队时显示不定进度动画；支持「取消」（上传取消不会留下半个文件）
- ✏️ **编辑**：预览弹层「✏️ 编辑」一键跳转**代码编辑器**插件（qwenpaw-code-editor）
  打开该文件，可直接编辑并保存；未安装代码编辑器插件时会提示先安装或下载到本地编辑
- 🔄 **一键刷新**当前目录
- 🎨 **视觉一致**：滚动条样式与 QwenPaw 控制台暗色主题对齐
- 🤖 **AI 助手**：工具栏「🤖 AI 助手」展开右下角可折叠对话面板
  - 发送消息自动附带**当前目录**与**选中文件/文件夹**作为上下文（空白对话框
    背景文字有说明）
  - 复用 QwenPaw agent 管线（工具调用/记忆/技能与主聊天一致），SSE 流式渲染，
    **思考过程**灰色区块单独展示；发送/停止同一按钮切换
  - **模型选择**：面板顶部下拉可选可用大模型，选择持久化
  - **审批处理**：AI 需要权限审批时面板内弹卡片，可一键允许/拒绝
  - **文件操作自动刷新**：AI 回复完成后静默刷新当前目录列表
  - 会话 ID 持久化，刷新后继续同一对话；「🗑 清空」可重置会话
- 🛡️ **安全**：工作区模式越界拦截；删除保护（禁止删除 `/`、主目录、QwenPaw 数据根目录）；
  写操作遵循系统权限，权限不足返回明确错误

![qwenpaw-file-browser](https://raw.githubusercontent.com/chcsyf/qwenpaw-file-browser/main/qwenpaw-file-browser.png)

## 接口

挂载于 `/api/qwenpaw-file-browser/`。`path` 支持**绝对路径**或**相对路径**
（相对 WORKING_DIR；空 = WORKING_DIR）。工作区模式下越界路径返回 400：

| 方法 | 路径 | 说明 |
|---|---|---|
| GET  | `/api/qwenpaw-file-browser/status` | 插件状态、版本、WORKING_DIR、当前模式（`mode`/`mode_source`/`platform_detected`）、可访问根目录列表 |
| GET  | `/api/qwenpaw-file-browser/editor/installed` | 检测代码编辑器插件（qwenpaw-code-editor）是否已安装（供「✏️ 编辑」跳转前探测） |
| GET  | `/api/qwenpaw-file-browser/ls?path=<dir>` | 列出目录（返回 `path`/`parent`/`entries`：name/type/size/size_h/mtime/path） |
| GET  | `/api/qwenpaw-file-browser/read?path=<file>&max_bytes=<n>` | 预览文本文件（默认不限大小 `max_bytes=-1`；显式传 `max_bytes` 可截断；二进制返回 415） |
| GET  | `/api/qwenpaw-file-browser/download?path=<file>` | 下载单个文件（附件） |
| POST | `/api/qwenpaw-file-browser/upload?path=<dir>` | 上传文件，multipart 多文件（`files` 字段）；filename 可带相对路径（如 `folder/sub/a.txt`）自动创建父目录保留目录结构，冲突自动重命名，防路径穿越；返回 `saved`/`dirs` |
| POST | `/api/qwenpaw-file-browser/mkdir` | 新建文件夹 `{"path": "...", "parents": false}` |
| POST | `/api/qwenpaw-file-browser/rename` | 重命名 `{"path": "...", "new_name": "..."}`（同目录内） |
| POST | `/api/qwenpaw-file-browser/delete` | 删除 `{"path": "...", "recursive": false}`（目录递归需显式 true） |
| POST | `/api/qwenpaw-file-browser/batch/delete` | 批量删除 `{"paths": ["..."], "recursive": false}`，返回 `deleted`/`failed` |
| GET  | `/api/qwenpaw-file-browser/batch/download?paths=a,b,c` | 批量打包下载 zip（目录递归收集；临时文件响应后自动清理） |
| POST | `/api/qwenpaw-file-browser/ai/chat` | AI 对话（SSE 流式）`{"text", "path", "selected": [], "session_id", "agent_id", "model"}`，复用 QwenPaw agent 管线，自动附带当前目录与选中文件上下文；`model` 为 `"provider_id:model"` 可选切换模型 |
| GET  | `/api/qwenpaw-file-browser/ai/models` | 可用模型列表（仅含已配置 key 或本地/免 key 的 provider），供 AI 面板下拉选择 |
| POST | `/api/qwenpaw-file-browser/mode` | 切换访问模式 `{"mode": "auto"\|"workdir"\|"platform"}`（auto = 自动识别）。模式为进程级全局开关（个人平台，单用户）；`auto` = 按环境自动判断 |

## 安装 / 升级

```bash
# 发布前校验（可选但建议）
qwenpaw plugin validate ./qwenpaw-file-browser
# 安装 / 覆盖更新（QwenPaw 运行中走 API 热装，无需重启）
qwenpaw plugin install ./qwenpaw-file-browser --force
```

> 注意：不要用 `uninstall` + `install` 两步走——`uninstall` 有交互确认
> （`click.confirm`），在脚本 / 非交互环境下会卡住，且旧版未卸载时
> `install` 会因 id 已存在而拒绝。`--force` 一步完成覆盖更新（rmtree
> 旧目录 → 复制新目录），非交互环境不卡。

刷新 QwenPaw 页面，侧边栏/设置菜单出现「📁 文件浏览器」入口，点击进入 `/apps/qwenpaw-file-browser`。
平台（platform.agentscope.io）部署：在插件管理页面上传 zip 即可。

## 目录结构

```
qwenpaw-file-browser/
├── plugin.json   # 清单：type=app, entry backend+frontend, menu, meta.pawapp
├── plugin.py     # 后端：status/ls/read/download/upload/mkdir/rename/delete/batch/mode
├── ui/index.js   # 前端：文件浏览器 GUI（React）
├── CHANGELOG.md  # 变更记录
├── LICENSE       # Apache License 2.0
└── .gitignore
```

## 变更记录

见 [CHANGELOG.md](https://github.com/chcsyf/qwenpaw-file-browser/blob/main/CHANGELOG.md)。

## 安全警告

- 插件具备**文件系统读写能力**（浏览、上传、新建、重命名、删除），
  以 **QwenPaw 进程身份**访问宿主文件系统，属于高危能力。
- 工作区模式默认仅允许访问 QwenPaw 根目录；平台模式遵循操作系统权限（系统盘只读）。
- **访问模式是进程级全局开关**：QwenPaw 目前是个人平台（单用户），不做多用户隔离；
  平台环境默认 platform，本地部署默认 workdir，可通过接口手动切换。
- **符号链接只作用于链接本身**：重命名 / 删除链接不会改动它指向的目标文件；
  越界判定对目标做 realpath 校验。列表用 🔗 标识链接，失效链接也会列出并标注。
- **批量下载逐项复检**：打包时 `os.walk` 不跟随符号链接，并对每个成员做 realpath
  校验；工作区模式下指向外部的软链接（如 `ln -s /etc`）会被跳过，不会被打包。
- 删除 / 重命名保护覆盖**精确路径 + 祖先路径**：`/`、主目录、QwenPaw 数据根目录、
  插件自身目录、`plugins/` 目录、密钥目录以及 `/app`、`/etc`、`/workspace` 等
  顶层系统目录，连同它们的祖先目录都不能删除或重命名。
- **预览渲染使用白名单清洗**：外部 Markdown 输出经 DOM 白名单过滤（未知标签、
  事件属性、style 等全部剥离），链接 / 图片 URL 走 `http(s)/mailto/tel` 与相对地址
  白名单，内置渲染器同样校验 URL scheme，阻断 `javascript:`、`<svg/onload=...>` 等 XSS。
- 删除操作不可恢复，前端均有二次确认；仅建议在本地 / 可信环境使用，不要部署到
  不可信公网平台。

## 已知限制

- 预览仅支持 UTF-8 文本；二进制文件请下载查看。默认不限大小，超大文件（数百 MB
  以上）会占用较多内存/带宽、渲染较慢；可传 `max_bytes` 只读前 N 字节。
- 内置渲染器支持 GFM 常用语法子集（复杂扩展如 Mermaid 图、脚注等需宿主提供 marked 等全局库时方可渲染）。
- 平台模式下写操作遵循系统权限：系统盘（`/app` 等）只读，写通常会失败并返回明确错误。
- 批量打包下载使用临时 zip 文件，超大目录可能较慢。
- 进度条依赖响应 `Content-Length` 显示百分比；若反向代理启用压缩/分块传输导致长度未知，会退化为「已接收字节 + 不定进度动画」。反代开启响应缓冲时，进度可能在缓冲刷出时跳跃。
