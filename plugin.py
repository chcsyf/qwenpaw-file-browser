"""
QwenPaw 文件浏览器插件 v0.2.5
浏览器窗口：分层级浏览/查看/下载 QwenPaw 工作区（QWENPAW_WORKING_DIR）以及
（平台模式下）容器内所有可访问的路径（NAS 持久层 / 容器本地盘 /tmp /home /root
/workspace / 系统盘只读等）。

访问模式：
  - 自动检测是否为 qwenpaw-agentscope-platform 环境（WORKING_DIR 位于
    /run/csi/mount-root/nas 之下即判定为平台）。
  - 平台环境 → 默认「平台模式」：可访问所有可访问路径。
  - 非平台环境 → 默认「工作区模式」：仅访问 WORKING_DIR（越界返回 400），
    与旧版行为一致，更安全。
  - 模式是进程级全局开关（个人平台，单用户）；平台环境默认 platform，
    本地部署默认 workdir，可通过 POST /mode 手动切换/恢复 auto。

接口（挂载于 /api/qwenpaw-file-browser/）：
  - GET  /status            插件状态、版本、WORKING_DIR、当前模式、快捷根目录列表
  - GET  /editor/installed  检测代码编辑器插件（qwenpaw-code-editor）是否已安装
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
  - 模式为进程级全局开关（个人平台，不做多用户隔离）
  - 删除/重命名保护：精确路径 + 祖先路径（/、HOME、WORKING_DIR、插件目录、
    plugins/、密钥目录、顶层系统目录）；目录默认只能删空目录
  - 批量下载不跟随符号链接，且对每个成员做 realpath 复检，越界项跳过
  - 批量下载用临时 zip 文件（避免内存占用），响应后自动清理
"""
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

PLUGIN_VERSION = "0.2.5"

router = APIRouter()

# 访问模式（进程级全局开关）：None = auto。
# QwenPaw 目前是个人平台（单用户），不需要按用户/客户端隔离；
# 保留全局开关即可，平台环境默认 platform，本地部署默认 workdir。
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


def _auto_mode() -> str:
    """auto = 平台环境用 platform，本地部署用 workdir。"""
    return "platform" if _is_platform_env() else "workdir"


def _effective_mode() -> str:
    """当前生效的访问模式：显式设置优先，否则按环境自动判断。"""
    return _mode["mode"] or _auto_mode()


def _check_access(request: Request, p: Path) -> None:
    """工作区模式下限制路径必须在 WORKING_DIR 内（与旧版行为一致）。

    判定用 realpath：``wd/../etc``、以及工作区内指向外部的符号链接都会
    被解析到真实位置后拒绝，不能靠软链接绕过。
    """
    if _effective_mode() != "workdir":
        return
    wd = _working_dir().resolve()
    try:
        real = p.resolve(strict=False)
    except (OSError, RuntimeError, ValueError) as e:
        raise HTTPException(status_code=400, detail=f"路径解析失败: {e}") from e
    try:
        real.relative_to(wd)
    except ValueError as e:
        raise HTTPException(
            status_code=400,
            detail=(
                "当前为工作区模式，仅允许访问 QwenPaw 工作区；"
                "如需访问根目录，请先调用 POST /api/qwenpaw-file-browser/mode"
                " 切换到 platform 模式"
            ),
        ) from e


def _resolve(request: Request, path: str) -> Path:
    """把输入路径解析为绝对路径：绝对路径直接使用；相对路径基于 WORKING_DIR。

    只做**词法**规范化（normpath），不解析最后一级符号链接：这样列目录、
    重命名、删除都作用于链接本身，而不是它指向的目标文件。越界判定由
    ``_check_access`` 用 realpath 完成。
    """
    p = (path or "").strip()
    if not p:
        wd = _working_dir().resolve()
        _check_access(request, wd)
        return wd
    try:
        pp = Path(p)
        if not pp.is_absolute():
            pp = _working_dir() / p
        pp = Path(os.path.normpath(str(pp)))
    except (OSError, ValueError) as e:
        # 含 NUL 字节等非法路径：返回 400 而不是 500
        raise HTTPException(status_code=400, detail=f"路径解析失败: {e}") from e
    _check_access(request, pp)
    return pp


# 顶层系统目录：整体删除会造成不可恢复的系统损坏，一律保护。
# 注意只保护目录本身及其祖先，不限制其子目录内的正常文件操作。
_SYSTEM_PROTECTED = tuple(
    Path(x)
    for x in (
        "/bin",
        "/boot",
        "/dev",
        "/etc",
        "/home",
        "/lib",
        "/lib64",
        "/proc",
        "/root",
        "/sbin",
        "/sys",
        "/usr",
        "/var",
        "/app",
        "/opt",
        "/srv",
        "/run",
        "/workspace",
        "/mnt",
        "/media",
        "/data",
    )
)


def _protected_paths() -> list[Path]:
    """不可删除 / 重命名的路径集合（精确匹配 + 祖先匹配）。"""
    candidates = [
        Path("/"),
        Path.home(),
        _working_dir(),
        Path(__file__).resolve().parent,          # 插件自身目录
        Path(__file__).resolve().parent.parent,   # plugins/ 目录（含其他插件与凭据）
    ]
    for env_name in ("QWENPAW_SECRET_DIR", "COPAW_SECRET_DIR"):
        value = os.environ.get(env_name)
        if value:
            candidates.append(Path(value))
    candidates.extend(_SYSTEM_PROTECTED)

    out: list[Path] = []
    for cand in candidates:
        try:
            resolved = cand.resolve()
        except OSError:
            continue
        if resolved not in out:
            out.append(resolved)
    return out


def _check_deletable(p: Path) -> None:
    """删除 / 重命名保护：精确路径 + 祖先路径 + 顶层系统目录。

    只做精确匹配挡不住「删 WORKING_DIR 的父目录」或「rename 掉 WORKING_DIR」，
    因此额外检查 p 是否为任一受保护路径的祖先。

    路径只解析**中间目录**的符号链接，末级保持原样：删除 / 重命名一个
    符号链接只影响链接本身，不该因为其目标受保护就被拒绝；而
    ``linkdir/file`` 这种经链接目录访问的路径，实际作用在目标目录，按真实
    位置校验。
    """
    raw = Path(os.path.normpath(str(p)))
    try:
        real_parent = raw.parent.resolve(strict=False)
    except (OSError, RuntimeError, ValueError) as e:
        raise HTTPException(status_code=400, detail=f"路径解析失败: {e}") from e
    target = real_parent / raw.name

    for protected in _protected_paths():
        if target == protected:
            raise HTTPException(
                status_code=400,
                detail=f"受保护路径，禁止删除或重命名: {target}",
            )
        try:
            protected.relative_to(target)
        except ValueError:
            continue
        # target 是 protected 的祖先：删除/移走它会连带破坏 protected
        raise HTTPException(
            status_code=400,
            detail=f"该目录包含受保护路径 {protected}，禁止删除或重命名",
        )


def _fmt_size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    for unit in ("KB", "MB", "GB"):
        n /= 1024.0
        if n < 1024:
            return f"{n:.1f} {unit}"
    return f"{n:.1f} TB"


def _roots(request: Request) -> list:
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


def _mode_info(request: Request) -> dict:
    return {
        "mode": _effective_mode(),
        "mode_source": "auto" if _mode["mode"] is None else "manual",
        "platform_detected": _is_platform_env(),
    }


# ---------- 接口 ----------
#
# 约定：凡是要做文件 I/O 的处理器一律用**同步 def**（FastAPI 会自动把它们丢到
# 线程池执行）；禁止用 async def 直接做阻塞式文件 I/O —— 那会在事件循环线程上跑，
# 冻结整个 QwenPaw 服务（工作区在 NAS/NFS 上时尤其明显）。
# 只有需要 await 的处理器（如 /ai/chat 的流式转发）才写 async def。
@router.get("/status")
def status(request: Request):
    # 同步 def：_roots() 会遍历 workspaces/ 并逐项 stat（NAS 上是阻塞 I/O）
    wd = _working_dir().resolve()
    info = _mode_info(request)
    return {
        "ok": True,
        "name": "文件浏览器",
        "version": PLUGIN_VERSION,
        "type": "file-browser",
        "workdir": str(wd),
        "cwd": os.getcwd(),
        "roots": _roots(request),
        **info,
    }


@router.get("/editor/installed")
def editor_installed():
    """检测代码编辑器插件（qwenpaw-code-editor）是否已安装。

    本插件与 code-editor 安装在同一 plugins/ 目录下（兄弟目录），
    通过检查该目录是否存在及其 plugin.json 是否可加载来判断。
    """
    here = Path(__file__).resolve()
    editor_dir = here.parent.parent / "qwenpaw-code-editor"
    installed = False
    if (editor_dir / "plugin.json").is_file():
        try:
            with open(editor_dir / "plugin.json", "r", encoding="utf-8") as f:
                meta = json.load(f)
            installed = bool(meta.get("id") == "qwenpaw-code-editor")
        except Exception:  # noqa: BLE001
            installed = False
    return {
        "ok": True,
        "installed": installed,
        "plugin": "qwenpaw-code-editor",
        "dir": str(editor_dir),
    }


@router.post("/mode")
def set_mode(req: ModeReq, request: Request):
    # 同步 def：切模式后会重新遍历 roots（阻塞 I/O），交给线程池
    if req.mode not in ("auto", "workdir", "platform"):
        raise HTTPException(status_code=400, detail="mode 必须为 auto/workdir/platform")
    _mode["mode"] = None if req.mode == "auto" else req.mode
    wd = _working_dir().resolve()
    return {
        "ok": True,
        "workdir": str(wd),
        "roots": _roots(request),
        **_mode_info(request),
    }


@router.get("/ls")
def ls(
    request: Request,
    path: str = Query("", description="目录路径（绝对或相对 WORKING_DIR，空 = WORKING_DIR）"),
):
    # 故意保持同步 def：目录遍历 + 逐项 stat 是阻塞式文件 I/O（NAS/NFS 上可能阻塞数秒）。
    # FastAPI 会把同步处理器丢到线程池执行，因此不会阻塞事件循环；
    # 若写成 async def，这些 I/O 会直接在事件循环线程上跑，冻结整个 QwenPaw 服务。
    p = _resolve(request, path)
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
        is_link = child.is_symlink()
        try:
            try:
                st = child.stat()
                broken = False
            except OSError:
                if not is_link:
                    raise
                # 悬空链接：仍然列出（用户可以删除它），信息取自链接本身
                st = child.lstat()
                broken = True
        except OSError:
            continue
        is_dir = child.is_dir()
        has_size = not is_dir and not broken
        entries.append({
            "name": child.name,
            "type": "dir" if is_dir else "file",
            "size": st.st_size if has_size else 0,
            "size_h": _fmt_size(st.st_size) if has_size else "-",
            "mtime": int(st.st_mtime),
            # 保留链接自身路径（不 resolve）：重命名/删除作用于链接，而不是目标
            "path": str(child),
            "link": is_link,
            "broken": broken,
        })
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
def read_file(
    request: Request,
    path: str = Query("", description="文件路径（绝对或相对）"),
    max_bytes: int = Query(_READ_MAX_BYTES, ge=-1, description="预览上限字节数，-1 = 不限制"),
):
    # 故意保持同步 def：文件 I/O 是阻塞操作（大文件 / NAS/NFS 上更明显），
    # 交给 FastAPI 线程池执行，避免阻塞事件循环。
    p = _resolve(request, path)
    if not p.exists():
        raise HTTPException(status_code=404, detail=f"路径不存在: {p}")
    if not p.is_file():
        raise HTTPException(status_code=400, detail=f"不是文件: {p}")
    try:
        size = p.stat().st_size
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"读取文件信息失败: {e}") from e
    truncated = False if max_bytes < 0 else size > max_bytes
    try:
        with p.open("rb") as fh:
            # max_bytes 限制时只读前缀，绝不把整个大文件读进内存
            raw = fh.read() if max_bytes < 0 else fh.read(max_bytes)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=f"没有权限读取文件: {p}") from e
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"读取文件失败: {e}") from e
    data = raw
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
def download(
    request: Request,
    path: str = Query("", description="文件路径（绝对或相对）"),
):
    p = _resolve(request, path)
    if not p.exists():
        raise HTTPException(status_code=404, detail=f"路径不存在: {p}")
    if not p.is_file():
        raise HTTPException(status_code=400, detail=f"不是文件: {p}")
    return FileResponse(p, filename=p.name, media_type="application/octet-stream")


@router.post("/upload")
def upload(
    request: Request,
    path: str = Query("", description="目标目录（绝对或相对 WORKING_DIR，空 = WORKING_DIR）"),
    files: list[UploadFile] | None = File(default=None),
):
    # 故意保持同步 def：写盘（shutil.copyfileobj）是阻塞式文件 I/O，
    # 由 FastAPI 线程池执行，避免阻塞事件循环。
    p = _resolve(request, path)
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
        try:
            dest = (root / rel).resolve()
        except (OSError, ValueError) as e:
            raise HTTPException(status_code=400, detail=f"非法路径: {raw}") from e
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
        # 先写同目录临时文件再原子 rename：上传被取消/中断时不会留下半个文件
        tmp_dest = dest.parent / (".qfb-upload-" + uuid.uuid4().hex + ".part")
        try:
            with tmp_dest.open("wb") as out:
                shutil.copyfileobj(f.file, out, length=1024 * 1024)
            os.replace(tmp_dest, dest)
            saved.append(str(dest.relative_to(root)))
        except PermissionError as e:
            raise HTTPException(status_code=403, detail=f"没有权限写入目录: {dest.parent}") from e
        except OSError as e:
            raise HTTPException(status_code=500, detail=f"保存文件失败: {e}") from e
        finally:
            try:
                if tmp_dest.exists():
                    tmp_dest.unlink()
            except OSError:
                pass
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
def mkdir(req: MkdirReq, request: Request):
    p = _resolve(request, req.path)
    if p.exists() or p.is_symlink():
        raise HTTPException(status_code=400, detail=f"路径已存在: {p}")
    try:
        p.mkdir(parents=req.parents, exist_ok=False)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=f"没有权限创建目录: {p}") from e
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"创建目录失败: {e}") from e
    return {"ok": True, "path": str(p)}


@router.post("/rename")
def rename(req: RenameReq, request: Request):
    new_name = (req.new_name or "").strip()
    if not new_name or "/" in new_name or "\\" in new_name or new_name in (".", ".."):
        raise HTTPException(status_code=400, detail="新名称不能为空且不能包含路径分隔符")
    p = _resolve(request, req.path)
    # 悬空符号链接 exists() 为 False，但仍应允许重命名/删除链接本身
    if not p.exists() and not p.is_symlink():
        raise HTTPException(status_code=404, detail=f"路径不存在: {p}")
    # 重命名等同于把原路径移走：受保护路径（含 WORKING_DIR 的祖先）一律拒绝
    _check_deletable(p)
    target = p.parent / new_name
    if target.exists() or target.is_symlink():
        raise HTTPException(status_code=400, detail=f"目标已存在: {target}")
    try:
        p.rename(target)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=f"没有权限重命名: {p}") from e
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"重命名失败: {e}") from e
    return {"ok": True, "from": str(p), "to": str(target)}


@router.post("/delete")
def delete(req: DeleteReq, request: Request):
    # 故意保持同步 def：rmtree 是阻塞式文件 I/O（NAS/NFS 上可能很慢），
    # 由 FastAPI 线程池执行，避免阻塞事件循环。
    p = _resolve(request, req.path)
    if not p.exists() and not p.is_symlink():
        raise HTTPException(status_code=404, detail=f"路径不存在: {p}")
    _check_deletable(p)
    try:
        if p.is_symlink():
            # 只删除链接本身，绝不跟随到目标（包括指向目录的链接、悬空链接）
            p.unlink()
        elif p.is_dir():
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
def batch_delete(req: BatchDeleteReq, request: Request):
    # 故意保持同步 def：批量 rmtree 是阻塞式文件 I/O，由 FastAPI 线程池执行。
    if not req.paths:
        raise HTTPException(status_code=400, detail="paths 不能为空")
    deleted, failed = [], []
    for raw in req.paths:
        try:
            p = _resolve(request, raw)
            if not p.exists() and not p.is_symlink():
                failed.append({"path": raw, "error": "不存在"})
                continue
            _check_deletable(p)
            if p.is_symlink():
                # 只删链接本身，不跟随目标
                p.unlink()
            elif p.is_dir():
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
def batch_download(
    request: Request,
    paths: Optional[list[str]] = Query(
        None,
        description="路径列表：可重复传 paths=a&paths=b；也兼容单个逗号分隔串",
    ),
):
    # 故意保持同步 def：zipfile 打包 + os.walk 递归是阻塞式文件 I/O（大目录/NAS 上可能
    # 数十秒），交给 FastAPI 线程池执行，避免阻塞事件循环。
    plist: list[str] = []
    for raw in paths or []:
        raw = (raw or "").strip()
        if not raw:
            continue
        # 优先按「单个路径」处理；只有整串不存在时才当作旧的逗号分隔列表，
        # 这样文件名里带逗号也不会被拆开。
        probe = Path(raw)
        if not probe.is_absolute():
            probe = _working_dir() / raw
        try:
            is_single = probe.exists() or probe.is_symlink()
        except (OSError, ValueError):
            is_single = False
        if is_single:
            plist.append(raw)
        else:
            plist.extend([x.strip() for x in raw.split(",") if x.strip()])
    if not plist:
        raise HTTPException(status_code=400, detail="paths 不能为空")
    resolved, missing = [], []
    for raw in plist:
        p = _resolve(request, raw)
        if not p.exists():
            missing.append(raw)
        else:
            resolved.append(p)
    if not resolved:
        raise HTTPException(status_code=404, detail=f"所有路径均不存在: {missing}")

    skipped: list[str] = []

    def _member_ok(fp: str) -> bool:
        """逐项复检：目录树中的符号链接可能指向工作区之外（如 ln -s /etc）。

        os.walk(followlinks=False) 不会进入符号链接目录，但文件级符号链接仍会被
        zipfile 解引用并读取目标内容，必须对每个成员做 realpath 后再按当前
        访问模式复检；越界项直接跳过，绝不打包。
        """
        try:
            real = Path(fp).resolve(strict=False)
        except (OSError, RuntimeError):
            return False
        try:
            _check_access(request, real)
        except HTTPException:
            return False
        return True

    tmp = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
    tmp_path = tmp.name
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
            for p in resolved:
                if p.is_file():
                    if not _member_ok(str(p)):
                        skipped.append(str(p))
                        continue
                    zf.write(p, p.name)
                elif p.is_dir():
                    base = p.parent
                    seen: set = set()  # (st_dev, st_ino)，防符号链接循环
                    for root, dirs, files in os.walk(p, followlinks=False):
                        # 显式剪掉符号链接目录（不跟随，避免越界与循环）
                        dirs[:] = [
                            d for d in dirs
                            if not os.path.islink(os.path.join(root, d))
                        ]
                        try:
                            st = os.stat(root)
                            key = (st.st_dev, st.st_ino)
                        except OSError:
                            key = None
                        if key is not None:
                            if key in seen:
                                dirs[:] = []
                                continue
                            seen.add(key)
                        dirs.sort()
                        files.sort()
                        for name in files:
                            fp = os.path.join(root, name)
                            if not _member_ok(fp):
                                skipped.append(fp)
                                continue
                            rel = os.path.relpath(fp, base)
                            try:
                                zf.write(fp, rel)
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

    headers = {}
    if skipped:
        logger.warning(
            "[qwenpaw-file-browser] batch/download skipped %d out-of-scope symlink(s)",
            len(skipped),
        )
        headers["X-QFB-Skipped-Count"] = str(len(skipped))

    return FileResponse(
        tmp_path,
        filename="files.zip",
        media_type="application/zip",
        background=BackgroundTask(_cleanup),
        headers=headers or None,
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
    model       模型选择（可选，格式 "provider_id:model"，空 = 使用 agent 默认模型）
    """

    text: str
    path: str = ""
    selected: List[str] = []
    session_id: str = ""
    agent_id: str = ""
    model: str = ""


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
            # 模型切换：model_slot_override 格式 "provider_id:model"，
            # Runtime 的 AgentRequest extra="allow" 会保留该字段并优先于 agent 默认模型
            if req.model and ":" in req.model:
                stream_req["model_slot_override"] = req.model
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


def _provider_usable(info) -> bool:
    """判断 provider 是否可用（已配 key 或本地/免 key 类型）。

    未提供 API key 且需要 key 的 provider（如官方预置但未配置的
    github-models/modelscope/dashscope 等）不可用，其模型不进入下拉列表。
    """
    if getattr(info, "is_local", False):
        return True
    if getattr(info, "oauth_connected", False):
        return True
    if not getattr(info, "require_api_key", True):
        return True
    return bool(getattr(info, "api_key", "") or "")


@router.get("/ai/models")
async def ai_models(request: Request) -> dict:
    """可用模型列表（供前端下拉选择，仅含已配置 key 或本地/免 key provider）。

    从主服务的 ProviderManager 收集所有可用 provider 的模型，
    返回 [{value: "provider_id:model_id", label: "provider_name / model_name"}]。
    """
    try:
        manager = getattr(request.app.state, "provider_manager", None)
        if manager is None:
            return {"ok": True, "models": [], "active": ""}
        infos = await manager.list_provider_info()
    except Exception as e:  # noqa: BLE001
        logger.error("[qwenpaw-file-browser] ai/models failed: %s", e)
        return {"ok": True, "models": [], "active": ""}

    models = []
    for info in infos or []:
        if not _provider_usable(info):
            continue
        if not getattr(info, "enabled", True):
            # 已禁用的 provider 不能实际出请求，不进下拉
            continue
        pid = getattr(info, "id", "") or ""
        pname = getattr(info, "name", "") or pid
        if not pid:
            continue
        all_models = list(getattr(info, "models", None) or []) + list(
            getattr(info, "extra_models", None) or []
        )
        hidden = set(getattr(info, "hidden_model_ids", None) or [])
        removed = set(getattr(info, "removed_model_ids", None) or [])
        seen = set()
        for m in all_models:
            mid = getattr(m, "id", "") or ""
            if not mid or mid in seen or mid in hidden or mid in removed:
                continue
            seen.add(mid)
            mname = getattr(m, "name", "") or mid
            models.append({
                "value": f"{pid}:{mid}",
                "label": f"{pname} / {mname}",
                "provider": pname,
                "model": mname,
                "is_free": bool(getattr(m, "is_free", False)),
            })
    return {"ok": True, "models": models, "active": ""}


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
