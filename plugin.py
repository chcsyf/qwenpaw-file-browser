"""
QwenPaw 文件浏览器插件 v0.2.0
浏览器窗口：分层级浏览/查看/下载 QwenPaw 工作区（QWENPAW_WORKING_DIR）以及
（平台模式下）容器内所有可访问的路径（NAS 持久层 / 容器本地盘 /tmp /home /root
/workspace / 系统盘只读等）。

访问模式：
  - 自动检测是否为 qwenpaw-agentscope-platform 环境（WORKING_DIR 位于
    /run/csi/mount-root/nas 之下即判定为平台）。
  - 平台环境 → 默认「平台模式」：可访问所有可访问路径。
  - 非平台环境 → 默认「工作区模式」：仅访问 WORKING_DIR（越界返回 400），
    与旧版行为一致，更安全。
  - 无法自动判断或需要临时切换时，前端提供按钮手动切换；也可用 POST /mode 恢复自动。

接口（挂载于 /api/qwenpaw-file-browser/）：
  - GET  /status            插件状态、版本、WORKING_DIR、当前模式、快捷根目录列表
  - GET  /ls?path=          列出目录（path 支持绝对路径或相对 WORKING_DIR；空 = WORKING_DIR）
  - GET  /read?path=        读取文本文件内容（预览，默认不限大小；可传 max_bytes 限制）
  - GET  /download?path=    下载文件（二进制安全，附件）
  - POST /upload            上传文件到目录 ?path=<dir>，multipart 多文件；filename 可带相对路径
                            （如 folder/sub/a.txt）自动创建父目录、保留目录结构；冲突自动重命名；防路径穿越
  - POST /mkdir             新建文件夹 {path, parents?}
  - POST /rename            重命名 {path, new_name}
  - POST /delete            删除 {path, recursive?}（目录默认非递归，须显式 recursive=true）
  - POST /batch/delete      批量删除 {paths: [], recursive?}
  - GET  /batch/download    批量打包下载 ?paths=a,b,c （目录递归收集）
  - POST /mode              切换访问模式 {mode: "auto"|"workdir"|"platform"}

安全说明：
  - 工作区模式只允许 WORKING_DIR 内路径；平台模式遵循操作系统权限：权限不足返回 403/500
  - 删除保护：禁止删除 / 、HOME 、WORKING_DIR 本身；目录默认只能删空目录
  - 批量下载用临时 zip 文件（避免内存占用），响应后自动清理
"""
import io
import logging
import asyncio
import json
import logging
import os
import shutil
import tempfile
import uuid
import zipfile
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from starlette.background import BackgroundTask
from pydantic import BaseModel

logger = logging.getLogger(__name__)

PLUGIN_VERSION = "0.2.0"

router = APIRouter()

# 访问模式（进程级）：None = auto（按环境自动判断）
_mode = {"mode": None}

# 读取预览上限：默认 -1 = 不限制大小（按需求全量读取）；
# 调用方可显式传 max_bytes 做按需截断
_READ_MAX_BYTES = -1


def _working_dir() -> Path:
    """运行时数据根目录（与 QwenPaw 一致：优先 QWENPAW_WORKING_DIR，兼容 COPAW_*）。"""
    wd = os.environ.get("QWENPAW_WORKING_DIR") or os.environ.get("COPAW_WORKING_DIR") or ""
    if wd:
        return Path(wd)
    home = Path.home()
    for cand in (home / ".qwenpaw", home / ".copaw"):
        if cand.is_dir():
            return cand
    return home


def _is_platform_env() -> bool:
    """检测是否运行在 qwenpaw-agentscope-platform 容器环境。

    判据：WORKING_DIR 位于 /run/csi/mount-root/nas 之下（平台 NAS 持久层挂载结构）。
    这是平台部署的稳定特征；本地部署（~/.qwenpaw 等）不满足该条件。
    """
    wd = _working_dir().resolve()
    nas = Path("/run/csi/mount-root/nas")
    if not nas.is_dir():
        return False
    try:
        wd.relative_to(nas)
        return True
    except ValueError:
        return False


def _effective_mode() -> str:
    """当前生效的访问模式：auto 时按环境判断。"""
    if _mode["mode"]:
        return _mode["mode"]
    return "platform" if _is_platform_env() else "workdir"


def _check_access(p: Path) -> None:
    """工作区模式下限制路径必须在 WORKING_DIR 内（与旧版行为一致）。"""
    if _effective_mode() != "workdir":
        return
    wd = _working_dir().resolve()
    try:
        p.relative_to(wd)
    except ValueError as e:
        raise HTTPException(
            status_code=400,
            detail="当前为工作区模式，仅允许访问 QwenPaw 根目录；可在界面开启「平台模式」以访问所有路径",
        ) from e


def _resolve(path: str) -> Path:
    """把输入路径解析为绝对路径：绝对路径直接使用；相对路径基于 WORKING_DIR。"""
    p = (path or "").strip()
    if not p:
        return _working_dir().resolve()
    pp = Path(p)
    if not pp.is_absolute():
        pp = _working_dir() / p
    try:
        rp = pp.resolve()
    except OSError as e:
        raise HTTPException(status_code=400, detail=f"路径解析失败: {e}") from e
    _check_access(rp)
    return rp


def _check_deletable(p: Path) -> None:
    """删除保护：禁止删除根目录、HOME、WORKING_DIR 本身。"""
    rp = p.resolve()
    if rp == Path("/").resolve():
        raise HTTPException(status_code=400, detail="不能删除根目录 /")
    if rp == Path.home().resolve():
        raise HTTPException(status_code=400, detail="不能删除主目录")
    if rp == _working_dir().resolve():
        raise HTTPException(status_code=400, detail="不能删除 QwenPaw 数据根目录")


def _fmt_size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    for unit in ("KB", "MB", "GB"):
        n /= 1024.0
        if n < 1024:
            return f"{n:.1f} {unit}"
    return f"{n:.1f} TB"


def _roots() -> list:
    """快捷根目录列表。

    - 工作区模式：WORKING_DIR + 其内的各智能体工作区快捷目录
      （布局B 时 workspaces/ 位于 WORKING_DIR 之下，访问合法；
       布局A 时其他工作区越界，不列出）
    - 平台模式：WORKING_DIR + 系统路径 + 各智能体工作区
    """
    mode = _effective_mode()
    wd = _working_dir().resolve()
    agent_dirs = _agent_workspace_dirs(wd)

    if mode == "workdir":
        out = [{"path": str(wd), "label": "WORKING_DIR"}]
        for d in agent_dirs:
            try:
                rr = d.resolve()
                rr.relative_to(wd)  # 仅限 WORKING_DIR 内（布局B 的 workspaces/ 子目录）
            except (OSError, ValueError):
                continue
            if rr == wd:
                continue
            out.append({"path": str(rr), "label": "🤖 " + rr.name + "（工作区）"})
        return out

    roots = [wd, Path("/tmp"), Path("/home"), Path("/root"), Path("/workspace")]
    roots.extend(agent_dirs)
    # NAS 挂载入口（若存在）
    nas = Path("/run/csi/mount-root/nas")
    if nas.is_dir():
        roots.append(nas)
    roots.append(Path("/app"))
    roots.append(Path("/"))
    agent_paths = set()
    for d in agent_dirs:
        try:
            agent_paths.add(d.resolve())
        except OSError:
            pass
    seen = set()
    out = []
    for r in roots:
        try:
            rr = r.resolve()
        except OSError:
            continue
        if rr in seen or not rr.exists():
            continue
        seen.add(rr)
        if rr == wd:
            label = "WORKING_DIR"
        elif rr == Path("/").resolve():
            label = "/ (文件系统根)"
        elif rr in agent_paths:
            label = "🤖 " + rr.name + "（工作区）"
        else:
            label = str(rr)
        out.append({"path": str(rr), "label": label})
    return out


def _agent_workspace_dirs(wd: Path) -> list:
    """扫描各智能体工作区目录（兼容两种部署布局）。

    布局A：WORKING_DIR 自身即某个智能体工作区（<...>/workspaces/<agent_id>）
           → 扫描父级 workspaces/（结果含 WORKING_DIR 自身）
    布局B：WORKING_DIR 为平台根（<...>）→ 扫描其下 workspaces/ 子目录
    """
    if wd.parent.name == "workspaces" and wd.parent.is_dir():
        agent_root = wd.parent
    else:
        ws = wd / "workspaces"
        if not ws.is_dir():
            return []
        agent_root = ws
    try:
        return sorted(
            (c for c in agent_root.iterdir()
             if c.is_dir() and not c.name.startswith(".")),
            key=lambda c: c.name,
        )
    except OSError:
        return []


# ---------- 请求模型 ----------
class MkdirReq(BaseModel):
    path: str
    parents: bool = False


class RenameReq(BaseModel):
    path: str
    new_name: str


class DeleteReq(BaseModel):
    path: str
    recursive: bool = False


class BatchDeleteReq(BaseModel):
    paths: list[str]
    recursive: bool = False


class ModeReq(BaseModel):
    mode: str  # "auto" | "workdir" | "platform"


def _mode_info() -> dict:
    return {
        "mode": _effective_mode(),
        "mode_source": "auto" if _mode["mode"] is None else "manual",
        "platform_detected": _is_platform_env(),
    }


# ---------- 接口 ----------
@router.get("/status")
async def status():
    wd = _working_dir().resolve()
    info = _mode_info()
    return {
        "ok": True,
        "name": "文件浏览器",
        "version": PLUGIN_VERSION,
        "type": "file-browser",
        "workdir": str(wd),
        "cwd": os.getcwd(),
        "roots": _roots(),
        **info,
    }


@router.post("/mode")
async def set_mode(req: ModeReq):
    if req.mode not in ("auto", "workdir", "platform"):
        raise HTTPException(status_code=400, detail="mode 必须为 auto/workdir/platform")
    _mode["mode"] = None if req.mode == "auto" else req.mode
    wd = _working_dir().resolve()
    return {
        "ok": True,
        "workdir": str(wd),
        "roots": _roots(),
        **_mode_info(),
    }


@router.get("/ls")
async def ls(path: str = Query("", description="目录路径（绝对或相对 WORKING_DIR，空 = WORKING_DIR）")):
    p = _resolve(path)
    if not p.exists():
        raise HTTPException(status_code=404, detail=f"路径不存在: {p}")
    if not p.is_dir():
        raise HTTPException(status_code=400, detail=f"不是目录: {p}")
    entries = []
    try:
        children = list(p.iterdir())
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=f"没有权限读取目录: {p}") from e
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"读取目录失败: {e}") from e
    for child in sorted(children, key=lambda x: (not x.is_dir(), x.name.lower())):
        try:
            st = child.stat()
            entries.append({
                "name": child.name,
                "type": "dir" if child.is_dir() else "file",
                "size": st.st_size if child.is_file() else 0,
                "size_h": _fmt_size(st.st_size) if child.is_file() else "-",
                "mtime": int(st.st_mtime),
                "path": str(child.resolve()),
            })
        except OSError:
            continue
    parent = None
    if p != Path("/").resolve():
        pp = p.parent
        # 工作区模式下，parent 若越界（如 WORKING_DIR 的上级）则置空，
        # 避免前端"上一级"跳转到不可访问路径
        if _effective_mode() == "workdir":
            wd = _working_dir().resolve()
            try:
                pp.relative_to(wd)
            except ValueError:
                pp = None
        if pp is not None:
            parent = str(pp)
    return {
        "ok": True,
        "path": str(p),
        "parent": parent,
        "entries": entries,
    }


@router.get("/read")
async def read_file(
    path: str = Query("", description="文件路径（绝对或相对）"),
    max_bytes: int = Query(_READ_MAX_BYTES, ge=-1, description="预览上限字节数，-1 = 不限制"),
):
    p = _resolve(path)
    if not p.exists():
        raise HTTPException(status_code=404, detail=f"路径不存在: {p}")
    if not p.is_file():
        raise HTTPException(status_code=400, detail=f"不是文件: {p}")
    try:
        size = p.stat().st_size
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"读取文件信息失败: {e}") from e
    try:
        raw = p.read_bytes()
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=f"没有权限读取文件: {p}") from e
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"读取文件失败: {e}") from e
    truncated = False if max_bytes < 0 else size > max_bytes
    data = raw if max_bytes < 0 else raw[:max_bytes]
    text = None
    if b"\x00" in data:
        # 含 NUL 字节，判定为二进制
        text = None
    elif truncated:
        # 截断可能切断多字节字符：逐字节回退再严格解码
        for cut in range(4):
            try:
                text = data.decode("utf-8") if cut == 0 else data[:-cut].decode("utf-8")
                break
            except UnicodeDecodeError:
                continue
    else:
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = None
    if text is None:
        raise HTTPException(status_code=415, detail="二进制文件（非 UTF-8 文本），请下载查看")
    return {
        "ok": True,
        "name": p.name,
        "path": str(p),
        "size": size,
        "truncated": truncated,
        "content": text,
    }


@router.get("/download")
async def download(path: str = Query("", description="文件路径（绝对或相对）")):
    p = _resolve(path)
    if not p.exists():
        raise HTTPException(status_code=404, detail=f"路径不存在: {p}")
    if not p.is_file():
        raise HTTPException(status_code=400, detail=f"不是文件: {p}")
    return FileResponse(p, filename=p.name, media_type="application/octet-stream")


@router.post("/upload")
async def upload(
    path: str = Query("", description="目标目录（绝对或相对 WORKING_DIR，空 = WORKING_DIR）"),
    files: list[UploadFile] | None = File(default=None),
):
    p = _resolve(path)
    if not p.exists():
        raise HTTPException(status_code=404, detail=f"路径不存在: {p}")
    if not p.is_dir():
        raise HTTPException(status_code=400, detail=f"不是目录: {p}")
    if not files:
        raise HTTPException(status_code=400, detail="未收到文件")
    root = p.resolve()
    saved: list[str] = []
    created_dirs: set[str] = set()
    for f in files:
        # filename 可为纯文件名，也可带相对路径（folder/sub/a.txt，文件夹上传/拖拽上传）
        raw = (f.filename or "").replace("\\", "/")
        # 显式拒绝绝对路径与父目录跳转，避免语义歧义
        if raw.startswith("/") or any(seg == ".." for seg in raw.split("/")):
            raise HTTPException(status_code=400, detail=f"非法路径: {raw}")
        parts = [seg for seg in raw.split("/") if seg and seg != "."]
        if not parts:
            continue
        rel = "/".join(parts)
        dest = (root / rel).resolve()
        # 防路径穿越：解析后必须仍在目标目录内
        try:
            dest.relative_to(root)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"非法路径: {raw}") from None
        # 自动创建缺失的父目录（已存在则合并，保留目录结构）
        parent = dest.parent
        missing: list[Path] = []
        cur = parent
        while not cur.exists() and cur != root:
            missing.append(cur)
            cur = cur.parent
        for d in reversed(missing):
            try:
                d.mkdir(exist_ok=True)
                created_dirs.add(str(d.relative_to(root)))
            except PermissionError as e:
                raise HTTPException(status_code=403, detail=f"没有权限创建目录: {d}") from e
            except OSError as e:
                raise HTTPException(status_code=500, detail=f"创建目录失败: {d}") from e
        if dest.exists():
            # 冲突自动重命名：name (1).ext
            base, ext = os.path.splitext(dest.name)
            i = 1
            while dest.exists():
                dest = dest.parent / f"{base} ({i}){ext}"
                i += 1
        try:
            with dest.open("wb") as out:
                shutil.copyfileobj(f.file, out, length=1024 * 1024)
            saved.append(str(dest.relative_to(root)))
        except PermissionError as e:
            raise HTTPException(status_code=403, detail=f"没有权限写入目录: {dest.parent}") from e
        except OSError as e:
            raise HTTPException(status_code=500, detail=f"保存文件失败: {e}") from e
        finally:
            try:
                f.file.close()
            except OSError:
                pass
    if not saved:
        raise HTTPException(status_code=400, detail="没有可保存的文件")
    return {
        "ok": True,
        "path": str(p),
        "saved": saved,
        "dirs": sorted(created_dirs),
    }


@router.post("/mkdir")
async def mkdir(req: MkdirReq):
    p = _resolve(req.path)
    if p.exists():
        raise HTTPException(status_code=400, detail=f"路径已存在: {p}")
    try:
        p.mkdir(parents=req.parents, exist_ok=False)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=f"没有权限创建目录: {p}") from e
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"创建目录失败: {e}") from e
    return {"ok": True, "path": str(p)}


@router.post("/rename")
async def rename(req: RenameReq):
    new_name = (req.new_name or "").strip()
    if not new_name or "/" in new_name or "\\" in new_name:
        raise HTTPException(status_code=400, detail="新名称不能为空且不能包含路径分隔符")
    p = _resolve(req.path)
    if not p.exists():
        raise HTTPException(status_code=404, detail=f"路径不存在: {p}")
    target = p.parent / new_name
    if target.exists():
        raise HTTPException(status_code=400, detail=f"目标已存在: {target}")
    try:
        p.rename(target)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=f"没有权限重命名: {p}") from e
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"重命名失败: {e}") from e
    return {"ok": True, "from": str(p), "to": str(target)}


@router.post("/delete")
async def delete(req: DeleteReq):
    p = _resolve(req.path)
    if not p.exists():
        raise HTTPException(status_code=404, detail=f"路径不存在: {p}")
    _check_deletable(p)
    try:
        if p.is_dir():
            if req.recursive:
                shutil.rmtree(p)
            else:
                if any(p.iterdir()):
                    raise HTTPException(status_code=400, detail=f"目录非空，需递归删除: {p.name}")
                p.rmdir()
        else:
            p.unlink()
    except HTTPException:
        raise
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=f"没有权限删除: {p}") from e
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"删除失败: {e}") from e
    return {"ok": True, "deleted": str(p)}


@router.post("/batch/delete")
async def batch_delete(req: BatchDeleteReq):
    if not req.paths:
        raise HTTPException(status_code=400, detail="paths 不能为空")
    deleted, failed = [], []
    for raw in req.paths:
        try:
            p = _resolve(raw)
            if not p.exists():
                failed.append({"path": raw, "error": "不存在"})
                continue
            _check_deletable(p)
            if p.is_dir():
                if req.recursive:
                    shutil.rmtree(p)
                else:
                    if any(p.iterdir()):
                        failed.append({"path": raw, "error": "目录非空（需递归删除）"})
                        continue
                    p.rmdir()
            else:
                p.unlink()
            deleted.append(raw)
        except HTTPException as e:
            failed.append({"path": raw, "error": str(e.detail)})
        except Exception as e:  # noqa: BLE001
            failed.append({"path": raw, "error": str(e)})
    return {"ok": True, "deleted": deleted, "failed": failed}


@router.get("/batch/download")
async def batch_download(paths: str = Query("", description="逗号分隔的路径列表（目录递归收集）")):
    plist = [x for x in (paths or "").split(",") if x.strip()]
    if not plist:
        raise HTTPException(status_code=400, detail="paths 不能为空")
    resolved, missing = [], []
    for raw in plist:
        p = _resolve(raw)
        if not p.exists():
            missing.append(raw)
        else:
            resolved.append(p)
    if not resolved:
        raise HTTPException(status_code=404, detail=f"所有路径均不存在: {missing}")

    tmp = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
    tmp_path = tmp.name
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
            for p in resolved:
                if p.is_file():
                    zf.write(p, p.name)
                elif p.is_dir():
                    base = p.parent
                    for f in p.rglob("*"):
                        if f.is_file():
                            try:
                                zf.write(f, str(f.relative_to(base)))
                            except OSError:
                                continue
    except Exception as e:  # noqa: BLE001
        os.unlink(tmp_path)
        raise HTTPException(status_code=500, detail=f"打包失败: {e}") from e

    def _cleanup() -> None:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    return FileResponse(
        tmp_path,
        filename="files.zip",
        media_type="application/zip",
        background=BackgroundTask(_cleanup),
    )


# ---------------------------------------------------------------------------
# AI 辅助对话（v0.2.0）
# ---------------------------------------------------------------------------

class AIChatRequest(BaseModel):
    """AI 对话请求体。

    text     用户消息正文
    path     当前目录（自动附加为上下文）
    selected 当前选中的文件/文件夹路径列表（自动附加为上下文）
    session_id  会话 ID（前端持久化，同一 ID 延续 QwenPaw 会话历史）
    agent_id    目标 agent（可选，默认 default / X-Agent-Id）
    """

    text: str
    path: str = ""
    selected: List[str] = []
    session_id: str = ""
    agent_id: str = ""


def _build_ai_prompt(req: AIChatRequest) -> str:
    """把当前路径 + 选中文件拼进用户提示词。"""
    parts = []
    if req.path:
        parts.append(f"当前目录：{req.path}")
    if req.selected:
        parts.append("选中文件：\n" + "\n".join(f"  - {p}" for p in req.selected))
    if parts:
        parts.append("---")
    parts.append(req.text.strip())
    return "\n".join(parts)


async def _get_workspace(request: Request, agent_id: str = "") -> object:
    """从主服务拿 agent workspace（与 QwenPaw 内部路由同一获取方式）。"""
    if not hasattr(request.app.state, "multi_agent_manager"):
        raise HTTPException(
            status_code=503,
            detail="MultiAgentManager 未初始化，AI 对话不可用",
        )
    manager = request.app.state.multi_agent_manager
    target = agent_id or request.headers.get("X-Agent-Id") or "default"
    try:
        workspace = await manager.get_agent(target)
    except (ValueError, KeyError) as e:
        raise HTTPException(status_code=404, detail=f"Agent 不存在: {target}") from e
    except Exception as e:  # noqa: BLE001
        logger.error("[qwenpaw-file-browser] get_agent(%s) failed: %s", target, e)
        raise HTTPException(status_code=500, detail=f"获取 Agent 失败: {e}") from e
    if workspace is None:
        raise HTTPException(status_code=404, detail=f"Agent 不存在: {target}")
    return workspace


def _serialize_event(ev: object) -> str:
    """把 stream_query 产出的 schema 对象序列化为 SSE data 行。"""
    try:
        if hasattr(ev, "model_dump"):
            payload = ev.model_dump()
        elif isinstance(ev, dict):
            payload = ev
        else:
            payload = {"object": "event", "data": str(ev)}
    except Exception as e:  # noqa: BLE001
        payload = {"object": "error", "error": f"序列化失败: {e}"}
    return "data: " + json.dumps(payload, ensure_ascii=False, default=str) + "\n\n"


@router.post("/ai/chat")
async def ai_chat(
    req: AIChatRequest,
    request: Request,
) -> StreamingResponse:
    """AI 辅助对话（SSE 流式）。

    复用 QwenPaw agent 管线（workspace.stream_query），同一 session_id 延续
    会话历史（工具调用/记忆/技能与主聊天一致）。事件为 QwenPaw 协议对象：
      {object: "response", status: "created"|"in_progress"|"completed"}
      {object: "message", role: "assistant", content: [{type:"text", text}]}
    """
    workspace = await _get_workspace(request, req.agent_id)
    session_id = req.session_id or ("qfb-ai-" + uuid.uuid4().hex)
    prompt = _build_ai_prompt(req)

    async def event_generator():
        try:
            stream_req = {
                "input": [
                    {
                        "role": "user",
                        "content": [{"type": "text", "text": prompt}],
                    }
                ],
                "session_id": session_id,
                "user_id": "qwenpaw-file-browser",
                "stream": True,
            }
            async for ev in workspace.stream_query(stream_req):
                yield _serialize_event(ev)
        except asyncio.CancelledError:
            # 客户端断开：agent 管线内部会做清理
            logger.info("[qwenpaw-file-browser] ai/chat cancelled (session=%s)", session_id)
            raise
        except Exception as e:  # noqa: BLE001
            logger.error(
                "[qwenpaw-file-browser] ai/chat error (session=%s): %s",
                session_id,
                e,
                exc_info=True,
            )
            yield "data: " + json.dumps(
                {"object": "error", "error": str(e)}, ensure_ascii=False
            ) + "\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


class FileBrowserPlugin:
    """文件浏览器插件"""

    def __init__(self):
        self.name = "文件浏览器"
        self.version = PLUGIN_VERSION
        self.id = "qwenpaw-file-browser"
        self.router = router

    def register(self, api) -> None:
        """注册插件"""
        if hasattr(api, "register_http_router"):
            api.register_http_router(
                self.router,
                prefix="/qwenpaw-file-browser",
                tags=["qwenpaw-file-browser"],
            )
            logger.info("[qwenpaw-file-browser] HTTP router registered at /api/qwenpaw-file-browser")

        if hasattr(api, "register_startup_hook"):
            api.register_startup_hook("qwenpaw_file_browser_startup", self._startup)

        if hasattr(api, "register_shutdown_hook"):
            api.register_shutdown_hook("qwenpaw_file_browser_shutdown", self._shutdown)

    async def _startup(self) -> None:
        logger.info(
            "[qwenpaw-file-browser] Plugin v%s started - workdir=%s",
            PLUGIN_VERSION,
            _working_dir().resolve(),
        )

    async def _shutdown(self) -> None:
        logger.info("[qwenpaw-file-browser] Plugin stopped")


# REQUIRED: 模块级 plugin 实例
plugin = FileBrowserPlugin()
