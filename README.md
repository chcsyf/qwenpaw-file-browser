# 📁 文件浏览器 (qwenpaw-file-browser) v0.0.1

QwenPaw 文件浏览器插件：在 QwenPaw 界面里分层级浏览、查看、上传、下载文件与目录，自动识别运行环境（qwenpaw-agentscope-platform 平台 / 本地部署），平台与本地通用。采用 [Apache License 2.0](LICENSE) 许可协议发布。

## 功能（v0.0.1）

- 📂 **分层级浏览目录**：图标 / 名称 / 大小 / 修改时间 / 绝对路径
- ⬆️ **上传文件**：多选上传到当前目录，文件名冲突自动重命名为 `name (1).ext`，流式写入
- 🌐 **访问范围自动适配**：
  - qwenpaw-agentscope-platform 平台（WORKING_DIR 位于 `/run/csi/mount-root/nas` 之下）
    默认「平台模式」：可访问所有支持访问的路径（NAS 持久层 / 容器本地盘 `/tmp` `/home`
    `/root` `/workspace` / 系统盘 `/app` 等只读区域），遵循操作系统权限
  - 本地部署默认「工作区模式」：仅访问 QwenPaw 根目录（越界返回 400）
  - 界面提供「🌐 平台模式 / 📦 工作区模式」手动切换
- 📁 **文件夹操作**：新建文件夹、重命名（文件/文件夹）、删除（目录递归删除带二次确认）
- ✅ **批量操作**：多选（勾选/全选）→ 批量打包下载（zip，目录递归收集）或批量删除
- 📄 **文本预览**：UTF-8 文本默认最多 256KB，超长截断；二进制/超大文件提示下载
- ⬇️ **下载**：任意文件（二进制安全，附件方式）
- 🔄 **一键刷新**当前目录
- 🛡️ **安全**：工作区模式越界拦截；删除保护（禁止删除 `/`、主目录、QwenPaw 数据根目录）；
  写操作遵循系统权限，权限不足返回明确错误

![qwenpaw-file-browser-0.0.1](qwenpaw-file-browser-0.0.1.png)

## 接口

挂载于 `/api/qwenpaw-file-browser/`。`path` 支持**绝对路径**或**相对路径**
（相对 WORKING_DIR；空 = WORKING_DIR）。工作区模式下越界路径返回 400：

| 方法 | 路径 | 说明 |
|---|---|---|
| GET  | `/api/qwenpaw-file-browser/status` | 插件状态、版本、WORKING_DIR、当前模式（`mode`/`mode_source`/`platform_detected`）、快捷根目录列表 |
| GET  | `/api/qwenpaw-file-browser/ls?path=<dir>` | 列出目录（返回 `path`/`parent`/`entries`：name/type/size/size_h/mtime/path） |
| GET  | `/api/qwenpaw-file-browser/read?path=<file>&max_bytes=<n>` | 预览文本文件（默认 256KB，超长截断；二进制返回 415） |
| GET  | `/api/qwenpaw-file-browser/download?path=<file>` | 下载单个文件（附件） |
| POST | `/api/qwenpaw-file-browser/upload?path=<dir>` | 上传文件，multipart 多文件（`files` 字段，冲突自动重命名） |
| POST | `/api/qwenpaw-file-browser/mkdir` | 新建文件夹 `{"path": "...", "parents": false}` |
| POST | `/api/qwenpaw-file-browser/rename` | 重命名 `{"path": "...", "new_name": "..."}`（同目录内） |
| POST | `/api/qwenpaw-file-browser/delete` | 删除 `{"path": "...", "recursive": false}`（目录递归需显式 true） |
| POST | `/api/qwenpaw-file-browser/batch/delete` | 批量删除 `{"paths": ["..."], "recursive": false}`，返回 `deleted`/`failed` |
| GET  | `/api/qwenpaw-file-browser/batch/download?paths=a,b,c` | 批量打包下载 zip（目录递归收集；临时文件响应后自动清理） |
| POST | `/api/qwenpaw-file-browser/mode` | 切换访问模式 `{"mode": "auto"\|"workdir"\|"platform"}`（auto = 自动识别） |

## 安装 / 升级

```bash
qwenpaw plugin validate ./qwenpaw-file-browser
qwenpaw plugin uninstall qwenpaw-file-browser   # 已有旧版时先卸载
qwenpaw plugin install ./qwenpaw-file-browser
```

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

见 [CHANGELOG.md](CHANGELOG.md)。

## 安全警告

- 插件具备**文件系统读写能力**（浏览、上传、新建、重命名、删除），
  以 **QwenPaw 进程身份**访问宿主文件系统，属于高危能力。
- 工作区模式默认仅允许访问 QwenPaw 根目录；平台模式遵循操作系统权限（系统盘只读）。
- 删除操作不可恢复，前端均有二次确认；删除保护禁止删除 `/`、主目录、数据根目录。
- 仅建议在本地 / 可信环境使用；不要部署到不可信公网平台。

## 已知限制

- 预览仅支持 UTF-8 文本（默认最大 256KB）；二进制/超大文件请下载查看。
- 平台模式下写操作遵循系统权限：系统盘（`/app` 等）只读，写通常会失败并返回明确错误。
- 批量打包下载使用临时 zip 文件，超大目录可能较慢。
