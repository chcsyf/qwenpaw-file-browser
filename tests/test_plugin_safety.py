"""qwenpaw-file-browser 安全 / 行为回归测试。

全部直接调用 plugin.py 里的处理器函数，不启动 HTTP 服务：
覆盖路径穿越、软链接语义、删除保护、/read 截断、批量下载参数等
审查中发现的问题。
"""
import os
import sys
import types
import zipfile
from pathlib import Path

import pytest

PLUGIN_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_DIR))

import plugin as P  # noqa: E402


class _State:
    def __init__(self, user=None):
        self.user = user


class _Client:
    def __init__(self, host):
        self.host = host


class FakeRequest:
    """只实现插件用到的最小 Request 形态。"""

    def __init__(self, host="127.0.0.1", user=None, headers=None):
        self.client = _Client(host)
        self.state = _State(user)
        self.headers = headers or {}
        self.app = types.SimpleNamespace(state=types.SimpleNamespace())


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    """每个用例独立的工作区，且客户端固定为本机。"""
    workdir = tmp_path / "wd"
    workdir.mkdir()
    monkeypatch.setenv("QWENPAW_WORKING_DIR", str(workdir))
    P._mode["mode"] = None
    return workdir


@pytest.fixture
def req():
    return FakeRequest()


def test_dotdot_and_absolute_escape_rejected(isolated_env, req):
    outside = isolated_env.parent / "outside"
    outside.mkdir()

    with pytest.raises(P.HTTPException) as exc:
        P._resolve(req, str(outside))
    assert exc.value.status_code == 400

    with pytest.raises(P.HTTPException) as exc:
        P._resolve(req, "../outside")
    assert exc.value.status_code == 400


def test_inside_path_allowed(isolated_env, req):
    target = isolated_env / "a.txt"
    target.write_text("hi", encoding="utf-8")
    assert P._resolve(req, "a.txt") == target
    assert P._resolve(req, str(target)) == target


def test_symlink_to_outside_rejected(isolated_env, req):
    outside = isolated_env.parent / "secret"
    outside.mkdir()
    (outside / "x").write_text("x", encoding="utf-8")
    link = isolated_env / "evil"
    link.symlink_to(outside, target_is_directory=True)

    with pytest.raises(P.HTTPException) as exc:
        P._resolve(req, str(link))
    assert exc.value.status_code == 400


def test_symlink_rename_affects_link_only(isolated_env, req):
    target = isolated_env / "a.txt"
    target.write_text("hi", encoding="utf-8")
    link = isolated_env / "link.txt"
    link.symlink_to(target.name)

    result = P.rename(P.RenameReq(path=str(link), new_name="renamed.txt"), req)

    assert result["ok"] is True
    assert target.read_text(encoding="utf-8") == "hi"
    assert not link.exists() and not link.is_symlink()
    renamed = isolated_env / "renamed.txt"
    assert renamed.is_symlink()


def test_symlink_delete_removes_link_only(isolated_env, req):
    target = isolated_env / "a.txt"
    target.write_text("hi", encoding="utf-8")
    link = isolated_env / "link.txt"
    link.symlink_to(target.name)

    result = P.delete(P.DeleteReq(path=str(link)), req)

    assert result["ok"] is True
    assert target.exists()
    assert not link.is_symlink()


def test_broken_symlink_can_be_deleted(isolated_env, req):
    link = isolated_env / "dangling"
    link.symlink_to("missing-target")

    result = P.delete(P.DeleteReq(path=str(link)), req)

    assert result["ok"] is True
    assert not link.is_symlink()


def test_ls_keeps_symlink_path(isolated_env, req):
    target = isolated_env / "a.txt"
    target.write_text("hi", encoding="utf-8")
    link = isolated_env / "link.txt"
    link.symlink_to(target.name)

    data = P.ls(req, str(isolated_env))
    entry = next(e for e in data["entries"] if e["name"] == "link.txt")

    assert entry["path"] == str(link)
    assert entry["link"] is True
    assert entry["broken"] is False


def test_delete_protects_ancestors_of_workdir(isolated_env, req):
    with pytest.raises(P.HTTPException) as exc:
        P._check_deletable(isolated_env.parent)
    assert exc.value.status_code == 400


def test_delete_protects_workspace_root():
    with pytest.raises(P.HTTPException) as exc:
        P._check_deletable(Path("/workspace"))
    assert exc.value.status_code == 400


def test_symlink_to_protected_dir_can_still_be_deleted(isolated_env):
    # 删除链接本身不会破坏受保护目标，应当放行
    link = isolated_env / "root-link"
    link.symlink_to("/", target_is_directory=True)
    P._check_deletable(link)  # 不抛异常即通过


def test_nul_byte_path_returns_400(req):
    with pytest.raises(P.HTTPException) as exc:
        P._resolve(req, "bad\x00name")
    assert exc.value.status_code == 400


def test_read_max_bytes_reads_prefix_only(isolated_env, req):
    big = isolated_env / "big.log"
    big.write_text("x" * (2 * 1024 * 1024), encoding="utf-8")

    data = P.read_file(req, path=str(big), max_bytes=10)

    assert data["truncated"] is True
    assert data["content"] == "x" * 10
    assert data["size"] == big.stat().st_size


def test_batch_download_accepts_repeated_paths(isolated_env, req):
    f1 = isolated_env / "a,b.txt"
    f1.write_text("1", encoding="utf-8")
    f2 = isolated_env / "two.txt"
    f2.write_text("2", encoding="utf-8")

    response = P.batch_download(req, paths=[str(f1), str(f2)])
    try:
        with zipfile.ZipFile(response.path) as zf:
            assert sorted(zf.namelist()) == sorted(["a,b.txt", "two.txt"])
    finally:
        response.background.func()


def test_mode_is_global(isolated_env):
    # 个人平台：模式是进程级全局开关，不再按用户/客户端隔离
    request = FakeRequest(host="203.0.113.7")
    P.set_mode(P.ModeReq(mode="platform"), request)
    assert P._effective_mode() == "platform"
    assert P._effective_mode() == "platform"  # 其他客户端同样看到 platform

    P.set_mode(P.ModeReq(mode="auto"), request)
    assert P._effective_mode() == "workdir"


def test_auto_mode_uses_platform_env(isolated_env, monkeypatch):
    monkeypatch.setattr(P, "_is_platform_env", lambda: True)
    assert P._mode["mode"] is None
    assert P._effective_mode() == "platform"


def test_mode_info_has_no_multi_user_fields(isolated_env):
    info = P._mode_info(FakeRequest())
    assert info["mode"] == "workdir"
    assert info["mode_source"] == "auto"
    assert "platform_mode_allowed" not in info
