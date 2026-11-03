"""对运行中的 QwenPaw 实例做端到端冒烟（可选，默认跳过）。

离线单元测试见 ``test_plugin_safety.py``；本文件只验证真实 HTTP 行为
（软链接重命名、批量下载参数编码），需要实例可用：

    QFB_LIVE_BASE=http://127.0.0.1:8088 python -m pytest tests -q

前置条件：从 loopback 访问（或已登录带 token），实例的 WORKING_DIR 可写。
测试会在 WORKING_DIR 下创建 ``qfb-live-*`` 临时目录，结束时通过插件自身
的删除接口清理。
"""
import io
import json
import os
import urllib.parse
import urllib.request
import uuid
import zipfile

import pytest

BASE = os.environ.get("QFB_LIVE_BASE", "").rstrip("/")
API = BASE + "/api/qwenpaw-file-browser"

pytestmark = pytest.mark.skipif(
    not BASE,
    reason="设置 QFB_LIVE_BASE（如 http://127.0.0.1:8088）后运行在线冒烟",
)


def _request(path, body=None):
    url = API + path
    if body is None:
        req = urllib.request.Request(url, method="GET")
    else:
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


def _json(path, body=None):
    return json.loads(_request(path, body))


@pytest.fixture
def live_dir():
    status = _json("/status")
    workdir = status.get("workdir") or ""
    if not workdir or not os.path.isdir(workdir):
        pytest.skip("实例未返回可写的 workdir")
    path = os.path.join(workdir, "qfb-live-" + uuid.uuid4().hex[:8])
    os.makedirs(path)
    try:
        yield path
    finally:
        try:
            _json("/delete", {"path": path, "recursive": True})
        except Exception:  # noqa: BLE001 - 清理失败不掩盖测试结果
            pass


def test_symlink_rename_keeps_target(live_dir):
    target = os.path.join(live_dir, "a.txt")
    link = os.path.join(live_dir, "link.txt")
    with open(target, "w", encoding="utf-8") as fh:
        fh.write("hi")
    os.symlink("a.txt", link)

    result = _json("/rename", {"path": link, "new_name": "renamed.txt"})

    assert result["ok"] is True
    with open(target, encoding="utf-8") as fh:
        assert fh.read() == "hi"
    assert not os.path.lexists(link)
    assert os.path.islink(os.path.join(live_dir, "renamed.txt"))


def test_batch_download_keeps_comma_filename(live_dir):
    comma = os.path.join(live_dir, "a,b.txt")
    plain = os.path.join(live_dir, "a.txt")
    for path, text in ((comma, "comma"), (plain, "plain")):
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)

    # 重复 paths 参数：文件名里的逗号不能被当作分隔符
    query = "&".join(
        "paths=" + urllib.parse.quote(p, safe="") for p in (comma, plain)
    )
    data = _request("/batch/download?" + query)

    names = sorted(zipfile.ZipFile(io.BytesIO(data)).namelist())
    assert names == ["a,b.txt", "a.txt"]