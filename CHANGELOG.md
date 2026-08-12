# 变更记录 (Changelog)

## v0.2.0 - 2026-08-12

- **AI 助手面板**：工具栏新增「🤖 AI 助手」可折叠对话面板
  - 发送消息自动附带**当前目录**与**选中的文件/文件夹**作为上下文（前端展示实时可见）
  - 复用 QwenPaw agent 管线（`workspace.stream_query`）：同一 `session_id` 延续
    会话历史，支持工具调用/记忆/技能，与主聊天能力一致
  - SSE 流式渲染（`object: content` 增量事件），跳过 reasoning 思考内容只展示正式回复；
    消息完成时用完整文本校正，保证最终内容准确
  - 支持「⏹ 停止」中断当前回复；会话 ID 存 localStorage，刷新页面后继续同一会话
  - 后端新增 `POST /ai/chat`（SSE），从主服务 `MultiAgentManager` 获取 workspace，
    非 QwenPaw 环境返回 503 明确提示
- 版本号统一为 0.2.0（plugin.py / ui/index.js / plugin.json / README）

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
