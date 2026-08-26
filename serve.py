#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from http import HTTPStatus
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import unquote, urlparse
from typing import Optional

ROOT = Path(__file__).resolve().parent
PUBLIC = ROOT / "public"
POSTS_FILE = PUBLIC / "posts.js"

HOST = "127.0.0.1"
PORT = int(os.environ.get("BLOG_PORT", "8080"))
MAX_BODY = 10 * 1024 * 1024

POSTS_PREFIX = """/* ============================================================
 * Qingyu'Blog · 文章数据（本地 Python 管理端自动生成）
 * 由 public/admin.js 通过 /local-api/posts 直接写入。
 * ============================================================ */
window.BLOG_POSTS = """
POSTS_SUFFIX = ";\n"


def load_posts() -> list[dict]:
    if not POSTS_FILE.exists():
        return []
    text = POSTS_FILE.read_text(encoding="utf-8")
    m = re.search(r"window\.BLOG_POSTS\s*=\s*(\[[\s\S]*\])\s*;\s*$", text)
    if not m:
        raise ValueError("无法解析 public/posts.js：未找到 window.BLOG_POSTS = [...]")
    data = json.loads(m.group(1))
    if not isinstance(data, list):
        raise ValueError("public/posts.js 中 BLOG_POSTS 不是数组")
    return data


def write_posts(posts: list[dict]) -> None:
    PUBLIC.mkdir(parents=True, exist_ok=True)
    if POSTS_FILE.exists():
        shutil.copy2(POSTS_FILE, POSTS_FILE.with_suffix(".js.bak"))

    payload = POSTS_PREFIX + json.dumps(posts, ensure_ascii=False, indent=2) + POSTS_SUFFIX
    fd, tmp_name = tempfile.mkstemp(prefix="posts-", suffix=".js.tmp", dir=str(PUBLIC))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, POSTS_FILE)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def normalize_post(raw: object, route_id: Optional[str] = None) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("文章数据必须是 JSON 对象")

    post = dict(raw)
    post_id = str(route_id or post.get("id") or "").strip()
    title = str(post.get("title") or "").strip()

    if not post_id:
        raise ValueError("文章 id 不能为空")
    if not title:
        raise ValueError("文章标题不能为空")
    if len(post_id) > 200:
        raise ValueError("文章 id 过长")

    post["id"] = post_id
    post["title"] = title
    post["date"] = str(post.get("date") or "")
    post["excerpt"] = str(post.get("excerpt") or "")
    post["content"] = str(post.get("content") or "")
    post["cover"] = str(post.get("cover") or "")
    post["category"] = str(post.get("category") or "")
    post["status"] = str(post.get("status") or "published")
    post["pinned"] = bool(post.get("pinned", False))

    tags = post.get("tags", [])
    if isinstance(tags, str):
        tags = [x.strip() for x in re.split(r"[,，]", tags) if x.strip()]
    elif isinstance(tags, list):
        tags = [str(x).strip() for x in tags if str(x).strip()]
    else:
        tags = []
    post["tags"] = tags
    return post


def upsert_post(post: dict) -> None:
    posts = load_posts()
    idx = next(
        (i for i, item in enumerate(posts)
         if isinstance(item, dict) and item.get("id") == post["id"]),
        -1,
    )
    if idx >= 0:
        posts[idx] = post
    else:
        posts.insert(0, post)
    write_posts(posts)


def delete_post(post_id: str) -> bool:
    posts = load_posts()
    filtered = [
        p for p in posts
        if not (isinstance(p, dict) and str(p.get("id")) == post_id)
    ]
    if len(filtered) == len(posts):
        return False
    write_posts(filtered)
    return True


class QingyuLocalHandler(SimpleHTTPRequestHandler):
    server_version = "QingyuLocal/1.0"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(PUBLIC), **kwargs)

    def log_message(self, fmt, *args):
        print("[Qingyu]", fmt % args)

    def _json(self, status: int, data: dict) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> object:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise ValueError("Content-Length 无效")
        if length <= 0:
            raise ValueError("请求体为空")
        if length > MAX_BODY:
            raise ValueError("请求体过大")
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception as e:
            raise ValueError("请求体不是有效 JSON") from e

    def _local_api_parts(self):
        path = unquote(urlparse(self.path).path)
        if path == "/local-api/status":
            return ("status", None)
        if path == "/local-api/posts":
            return ("posts", None)
        if path.startswith("/local-api/posts/"):
            return ("post", path[len("/local-api/posts/"):])
        return (None, None)

    def do_GET(self):
        kind, post_id = self._local_api_parts()

        if kind == "status":
            self._json(HTTPStatus.OK, {
                "ok": True,
                "writable": True,
                "storage": "public/posts.js",
            })
            return

        if kind == "posts":
            try:
                self._json(HTTPStatus.OK, {
                    "ok": True,
                    "posts": load_posts(),
                    "localFile": True,
                })
            except Exception as e:
                self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": str(e)})
            return

        if kind == "post":
            try:
                posts = load_posts()
                found = next(
                    (p for p in posts if isinstance(p, dict) and str(p.get("id")) == post_id),
                    None,
                )
                if found is None:
                    self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "文章不存在"})
                else:
                    self._json(HTTPStatus.OK, {"ok": True, "post": found, "localFile": True})
            except Exception as e:
                self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": str(e)})
            return

        if urlparse(self.path).path.startswith("/api/"):
            self._json(HTTPStatus.NOT_FOUND, {
                "ok": False,
                "error": "Cloudflare API is not available in local Python mode",
            })
            return

        path = unquote(urlparse(self.path).path)
        target = (PUBLIC / path.lstrip("/")).resolve()
        try:
            target.relative_to(PUBLIC.resolve())
        except ValueError:
            self.send_error(HTTPStatus.FORBIDDEN)
            return

        if path != "/" and not target.exists():
            self.path = "/index.html"

        super().do_GET()

    def do_POST(self):
        kind, _ = self._local_api_parts()
        if kind != "posts":
            self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Not Found"})
            return
        try:
            post = normalize_post(self._read_json())
            upsert_post(post)
            self._json(HTTPStatus.OK, {
                "ok": True,
                "post": post,
                "localFile": True,
                "message": "已写入 public/posts.js",
            })
        except ValueError as e:
            self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(e)})
        except Exception as e:
            self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": str(e)})

    def do_PUT(self):
        kind, post_id = self._local_api_parts()
        if kind != "post" or not post_id:
            self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Not Found"})
            return
        try:
            post = normalize_post(self._read_json(), route_id=post_id)
            upsert_post(post)
            self._json(HTTPStatus.OK, {
                "ok": True,
                "post": post,
                "localFile": True,
                "message": "已更新 public/posts.js",
            })
        except ValueError as e:
            self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(e)})
        except Exception as e:
            self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": str(e)})

    def do_DELETE(self):
        kind, post_id = self._local_api_parts()
        if kind != "post" or not post_id:
            self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Not Found"})
            return
        try:
            removed = delete_post(post_id)
            if not removed:
                self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "文章不存在"})
                return
            self._json(HTTPStatus.OK, {
                "ok": True,
                "id": post_id,
                "localFile": True,
                "message": "已从 public/posts.js 删除",
            })
        except Exception as e:
            self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": str(e)})


def main():
    if not PUBLIC.exists():
        raise SystemExit(f"找不到 public 目录：{PUBLIC}")

    server = ThreadingHTTPServer((HOST, PORT), QingyuLocalHandler)
    print("")
    print("Qingyu'Blog 可写本地服务已启动")
    print(f"  首页: http://localhost:{PORT}/")
    print(f"  后台: http://localhost:{PORT}/admin")
    print(f"  数据: {POSTS_FILE}")
    print("  写入时自动生成 public/posts.js.bak")
    print("")
    print("按 Ctrl+C 停止。")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
