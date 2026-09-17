# 变更记录 (Changelog)

## v0.2.4 - 2026-09-17

- **修复：7 个文件 I/O 处理器阻塞事件循环**
  - 受影响：`/ls`、`/read`、`/download`、`/upload`、`/mkdir`、`/rename`、`/delete`、
    `/batch/delete`、`/batch/download` —— `iterdir`/`stat`/`read_bytes`/`write`/`copyfileobj`/
    `rmtree`/`os.walk`+`zipfile` 全是阻塞式 I/O，工作区在 NAS/NFS 上时单次可达数秒至数十秒。
  - 修复：这些处理器由 `async def` 改为**同步 `def`** —— FastAPI 会自动把它们丢到线程池执行，
    不再占用事件循环线程（这些处理器均无 `await` 依赖，改动等价且更彻底）。
  - 保留 `async def` 的仅 `/ai/chat`、`/ai/models`（需要 `await` 做流式转发）。

## v0.2.3 - 2026-08-25

- **修复鉴权**：修复 QwenPaw `AuthMiddleware` 开启（`QWENPAW_AUTH_ENABLED=true`）且客户端 IP 不在
  `security.allow_no_auth_hosts`（如反代 / 非 loopback）时，插件全部接口返回 401 的 bug。
  根因是前端 `fetchJson()` 与下载请求未携带 Bearer token，绕过了平台鉴权。现通过
  `getAuthToken()`（优先 `host.getApiToken()`，回退 `localStorage["qwenpaw_auth_token"]`）统一注入
  `Authorization: Bearer <token>`，覆盖 JSON 接口、AI 聊天（fetch + ReadableStream）、上传、下载。
- **支持带 token 的文件下载**：单个 / 批量下载由 `<a href>` 跳转改为 `fetch` 携带 token 取 blob 后
  触发下载（`<a href>` 无法携带 Authorization 头，鉴权开启时下载必 401）；`URL.revokeObjectURL` 及时释放。
- 版本号统一为 0.2.3（plugin.py / plugin.json / README / ui/index.js）

## v0.2.2 - 2026-08-15

- **快捷访问**：路径行新增「⭐ 添加到快捷访问」按钮（将当前路径加入快捷访问，
  localStorage 持久化）；工具栏「快捷根目录…」下拉改为「⚡ 快捷访问」面板——
  上半区**可访问根目录**（WORKING_DIR / 智能体工作区 / 系统路径 / NAS），
  下半区**用户自定义**条目（📌 点击跳转，右侧 ✕ 可移除），点击面板外自动收起
- **列表排序**：表头「名称 / 大小 / 修改时间」点击排序（再点切换升/降序 ▲▼，
  换列默认升序；目录始终优先，次级按名称）
- **AI 面板模型下拉只显示可用大模型**：`GET /ai/models` 过滤未配置 API key 的
  provider，仅保留已配置 key / 本地 / 免 key / OAuth 已连接的 provider
- **AI 助手头部描述精简**：移除头部「自动附带当前路径与选中文件」说明文字
  （空白对话框背景文字已含完整说明）
- 版本号统一为 0.2.2（plugin.py / plugin.json / README）

## v0.2.1 - 2026-08-12

- **✏️ 预览弹层「编辑」跳转代码编辑器**：文件预览弹层新增「✏️ 编辑」按钮，跳转到
  qwenpaw-code-editor 插件打开该文件；经同源 `localStorage` 传递目标路径（宿主 SPA
  路由重写 URL 会丢弃 query，故不用 `?file=`），code-editor 初始化读后即删、
  自动定位文件树并打开
- **未安装提示**：跳转前经 `/editor/installed` 探测 code-editor 是否安装
  （检查兄弟插件目录 plugin.json）；未安装时 toast 提示「请先安装 qwenpaw-code-editor，
  或下载到本地编辑」，不跳转
- 文件列表行内**不再显示**编辑按钮（避免行内拥挤），编辑入口仅在预览弹层

## v0.2.0 - 2026-08-12

- **AI 助手面板**：工具栏新增「🤖 AI 助手」可折叠对话面板（右下角浮动，高度自适应）
  - 发送消息自动附带**当前目录**与**选中的文件/文件夹**作为上下文（前端展示实时可见）
  - 复用 QwenPaw agent 管线（`workspace.stream_query`）：同一 `session_id` 延续
    会话历史，支持工具调用/记忆/技能，与主聊天能力一致
  - SSE 流式渲染（`object: content` 增量事件），**思考过程**以灰色「🤔 思考过程」
    区块单独展示，正式回复 Markdown 渲染；消息完成时用完整文本校正
  - **模型选择**：面板顶部下拉可选可用大模型（`GET /ai/models` 收集所有 provider
    模型），选择持久化到 localStorage，通过 `model_slot_override` 按请求切换
  - **发送/停止同一按钮**：空闲显示「发送」，生成中切换为「停止」（红色，无方块图标）
  - **文件操作后自动刷新**：AI 回复完成后静默刷新当前目录列表（AI 通过工具
    创建/删除/重命名文件后立即可见）
  - **审批处理**：AI 需要审批权限时（tool_guard ASK 模式挂起等待），面板内轮询
    `/api/approval/list` 弹出「⚠️ 需要审批」卡片，可一键允许/拒绝（`/approve` `/deny`）
  - **上下文持久化 + 清空**：会话 ID 存 localStorage，刷新页面后继续同一会话；
    「🗑 清空」按钮重置会话（换新 session_id 并清空消息）
  - 支持「停止」中断当前回复；后端新增 `POST /ai/chat`（SSE），从主服务
    `MultiAgentManager` 获取 workspace，非 QwenPaw 环境返回 503 明确提示
- 版本号统一为 0.2.0（plugin.py / ui/index.js / plugin.json / README）
- 预览图更新为 `qwenpaw-file-browser.png`，README 图片引用改为 GitHub 绝对链接
  （raw.githubusercontent.com），不再使用相对路径

## v0.1.5 - 2026-08-12

- **修复进入已删除文件夹导致页面崩溃**：后端对不存在的路径返回 404
  `{"detail": "路径不存在: ..."}`（无 `ok` 字段），前端 `fetchJson` 不检查
  HTTP 状态、`data.ok === false` 判定不成立，把错误体当作成功数据设置进
  `entries`，渲染时 `entries.entries.forEach` 触发 TypeError 崩溃；且
  localStorage 中的失效路径不会被清除，导致刷新/重新进入依然崩溃
- 修复内容：`fetchJson` 非 2xx 一律抛错（解析 `detail`/`error`/`message`）；
  `fetchList` 防御性校验 `entries` 必须为数组；渲染处对 `entries.entries`
  增加存在性保护；保存路径失效时自动清除记录，下次刷新回到 WORKING_DIR
- 版本号统一为 0.1.5（plugin.py / ui/index.js / plugin.json）

## v0.1.4 - 2026-08-07

- **刷新保持当前位置**：当前打开的目录记入 localStorage，刷新页面后自动恢复
  到上次打开的位置（不再每次回到根目录）；保存路径失效时自动清理记录
  （工作区模式越界/失效路径仍自动回 WORKING_DIR）
- 版本号统一为 0.1.4（plugin.py / ui/index.js / plugin.json）

## v0.1.3 - 2026-08-07

- **拖拽上传**：把文件 / 文件夹直接拖入浏览器窗口即可上传到当前目录
  （悬停显示高亮提示层；支持多层目录递归收集，保留目录结构）
- **上传文件夹**：工具栏新增「📁 上传文件夹」按钮，选择整个文件夹上传，
  自动保留目录结构（`webkitRelativePath` 相对路径），同名目录自动合并、
  文件冲突自动重命名
- 后端 `/upload` 支持 filename 带相对路径（`folder/sub/a.txt`）：
  自动创建缺失父目录、防路径穿越（`resolve()` 后必须仍在目标目录内）；
  返回新增 `dirs` 字段（本次新建的文件夹列表）
- 版本号统一为 0.1.3（plugin.py / ui/index.js / plugin.json）

## v0.1.2 - 2026-08-06

- **快捷目录新增各智能体工作区**：工作区模式下自动扫描 WORKING_DIR 下的
  `workspaces/` 目录，以「🤖 <agent_id>（工作区）」形式加入快捷根目录下拉，
  一键直达任意智能体的工作区（平台模式下同样列出）
- 版本号统一为 0.1.2（plugin.py / ui/index.js / plugin.json）

## v0.1.0 - 2026-08-06

- **预览渲染升级**：
  - Markdown（`.md` / `.markdown`）预览渲染（标题/列表/任务列表/表格/引用/代码块/链接等，GitHub Dark 风格）
  - JSON / JSONC 预览自动格式化（缩进 2 空格）+ 语法高亮
  - 场景/配置文件（`.yaml` / `.yml` / `.toml` / `.ini` / `.conf` / `.cfg` / `.env` / `.properties` / `.xml`）语法高亮渲染
  - 常见代码文件（`.py` / `.js` / `.ts` / `.sh` / `.go` / `.rs` / `.c` 等）语法高亮
  - 渲染策略：运行时优先复用宿主全局的 marked / hljs / Prism（若存在），否则内置轻量渲染器，零外部依赖、离线可用；外部库输出经 HTML 清洗
- **预览不再限制大小**：后端 `/read` 默认全量读取（`max_bytes=-1`），移除 256KB 截断与 10MB 硬限制
- 预览弹层显示格式标签（Markdown / JSON / YAML / 配置 / 文本 等）
- 版本号统一为 0.1.0（plugin.py / ui/index.js / plugin.json）

## v0.0.1 - 2026-08-06（首个发布版）

- 分层级目录浏览（图标/名称/大小/修改时间/绝对路径），点击文本文件预览
- 环境自动识别：qwenpaw-agentscope-platform 平台默认「平台模式」
  （可访问所有支持访问的路径：NAS 持久层/容器本地盘/系统盘只读区），
  本地部署默认「工作区模式」（仅 QwenPaw 根目录，越界返回 400）；支持手动切换
- 文件操作：上传（多选/冲突自动重命名/流式写入）、下载（二进制安全）、文本预览
- 文件夹操作：新建文件夹、重命名（文件/文件夹）、删除（目录递归删除带二次确认）
- 批量操作：多选 → 批量打包下载（zip，目录递归收集）/ 批量删除（deleted/failed 结果）
- 安全：工作区越界拦截；删除保护（禁止删除 `/`、主目录、数据根目录）；
  写操作遵循系统权限；上传路径穿越防护
- 采用 Apache License 2.0 发布
