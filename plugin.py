"""
QwenPaw 文件浏览器插件 v0.2.3
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
  - GET  /read?path=        读取文本文件内容（预览，默认最多 256KB，超长截断）
  - GET  /download?path=    下载文件（二进制安全，附件）
  - POST /upload            上传文件到目录 ?path=<dir>，multipart 多文件（冲突自动重命名）
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
import os
import shutil
import tempfile
import zipfile
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from starlette.background import BackgroundTask
from pydantic import BaseModel

logger = logging.getLogger(__name__)

PLUGIN_VERSION = "0.2.3"

router = APIRouter()

# 访问模式（进程级）：None = auto（按环境自动判断）
_mode = {"mode": None}

# 读取预览上限：默认 256KB，超过截断；超过该大小的文件不直接预览
_READ_MAX_BYTES = 256 * 1024
_READ_HARD_LIMIT = 10 * 1024 * 1024


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
    """快捷根目录（按当前模式过滤：工作区模式只返回 WORKING_DIR）。"""
    mode = _effective_mode()
    wd = _working_dir().resolve()
    if mode == "workdir":
        return [{
            "path": str(wd),
            "label": "WORKING_DIR",
        }]
    roots = [wd, Path("/tmp"), Path("/home"), Path("/root"), Path("/workspace")]
    # NAS 挂载入口（若存在）
    nas = Path("/run/csi/mount-root/nas")
    if nas.is_dir():
        roots.append(nas)
    roots.append(Path("/app"))
    roots.append(Path("/"))
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
        out.append({
            "path": str(rr),
            "label": "WORKING_DIR" if rr == wd else ("/ (文件系统根)" if rr == Path("/") else str(rr)),
        })
    return out


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
    max_bytes: int = Query(_READ_MAX_BYTES, ge=1, le=_READ_MAX_BYTES),
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
    if size > _READ_HARD_LIMIT:
        raise HTTPException(status_code=413, detail=f"文件过大（{_fmt_size(size)}），请下载查看")
    try:
        raw = p.read_bytes()
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=f"没有权限读取文件: {p}") from e
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"读取文件失败: {e}") from e
    truncated = size > max_bytes
    data = raw[:max_bytes]
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
    saved: list[str] = []
    for f in files:
        raw = os.path.basename((f.filename or "").replace("\\", "/"))
        if not raw:
            continue
        dest = p / raw
        if dest.exists():
            # 冲突自动重命名：name (1).ext
            base, ext = os.path.splitext(raw)
            i = 1
            while dest.exists():
                dest = p / f"{base} ({i}){ext}"
                i += 1
        try:
            with dest.open("wb") as out:
                shutil.copyfileobj(f.file, out, length=1024 * 1024)
            saved.append(dest.name)
        except PermissionError as e:
            raise HTTPException(status_code=403, detail=f"没有权限写入目录: {p}") from e
        except OSError as e:
            raise HTTPException(status_code=500, detail=f"保存文件失败: {e}") from e
        finally:
            try:
                f.file.close()
            except OSError:
                pass
    if not saved:
        raise HTTPException(status_code=400, detail="没有可保存的文件")
    return {"ok": True, "path": str(p), "saved": saved}


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
