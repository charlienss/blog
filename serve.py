#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import binascii
import json
import os
import re
import secrets
import shutil
import tempfile
from datetime import datetime
from http import HTTPStatus
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from typing import Optional
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parent
PUBLIC = ROOT / "public"
POSTS_FILE = PUBLIC / "posts.js"
IMAGES_DIR = PUBLIC / "images"
MARKDOWN_DIR = ROOT / "markdown"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = int(os.environ.get("BLOG_PORT", "8080"))

# 8 MB 图片转 base64 后会膨胀约 1/3，因此请求体上限要更大
MAX_BODY = 20 * 1024 * 1024
MAX_IMAGE_BYTES = 8 * 1024 * 1024

IMAGE_TYPES = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/avif": ".avif",
}

POSTS_PREFIX = (
    "/* ============================================================\n"
    " * Qingyu'Blog · 文章数据（本地 Python 管理端自动生成）\n"
    " * 由 public/admin.js 通过 /local-api/posts 直接写入。\n"
    " * ============================================================ */\n"
    "window.BLOG_POSTS = "
)
POSTS_SUFFIX = ";\n"


def load_posts() -> list[dict]:
    if not POSTS_FILE.exists():
        return []

    text = POSTS_FILE.read_text(encoding="utf-8")
    m = re.search(r"window\.BLOG_POSTS\s*=\s*(\[[\s\S]*\])\s*;\s*$", text)
    if not m:
        raise ValueError(
            "无法解析 public/posts.js：未找到 window.BLOG_POSTS = [...]"
        )

    data = json.loads(m.group(1))
    if not isinstance(data, list):
        raise ValueError("public/posts.js 中 BLOG_POSTS 不是数组")
    return data


def write_posts(posts: list[dict]) -> None:
    PUBLIC.mkdir(parents=True, exist_ok=True)

    if POSTS_FILE.exists():
        shutil.copy2(POSTS_FILE, POSTS_FILE.with_suffix(".js.bak"))

    payload = (
        POSTS_PREFIX
        + json.dumps(posts, ensure_ascii=False, indent=2)
        + POSTS_SUFFIX
    )

    fd, tmp_name = tempfile.mkstemp(
        prefix="posts-",
        suffix=".js.tmp",
        dir=str(PUBLIC),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, POSTS_FILE)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)

    try:
        sync_markdown_files(posts)
    except Exception as e:
        print("[Qingyu] Markdown mirror warning:", e)


def _markdown_safe_filename(value: object) -> str:
    raw = str(value or "").strip() or "post"

    # Windows / macOS / Linux 都尽量安全，同时保留中文。
    safe = re.sub(
        r'[<>:"/\\|?*\x00-\x1f]+',
        "-",
        raw,
    )
    safe = re.sub(r"\s+", "-", safe).strip(" .-")
    return (safe[:120].rstrip(" .-") or "post") + ".md"


def _markdown_body_for_repo(content: object) -> str:
    text = str(content or "")

    # 博客正文里 /images/... 是站点根路径。
    # Markdown 镜像位于 blog/markdown/，所以转成仓库相对路径。
    # 这里不用正则，避免转义问题。
    text = text.replace(
        "](/images/",
        "](../public/images/",
    )

    text = text.replace(
        'src="/images/',
        'src="../public/images/',
    )

    text = text.replace(
        "src='/images/",
        "src='../public/images/",
    )

    return text


def render_markdown_copy(post: dict) -> str:
    """
    生成纯正文 Markdown 副本。

    不再写 YAML Front Matter。
    仅保留一个 GitHub 预览时不可见的 HTML 注释，
    用于区分自动生成的 Markdown，避免删除用户手写文件。
    """
    body = _markdown_body_for_repo(
        post.get("content")
    ).strip()

    if body:
        return (
            "<!-- markdown_generated: true -->\n\n"
            + body
            + "\n"
        )

    return "<!-- markdown_generated: true -->\n"


def sync_markdown_files(posts: list[dict]) -> None:
    MARKDOWN_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    expected = set()

    for post in posts:
        if not isinstance(post, dict):
            continue

        filename = _markdown_safe_filename(
            post.get("id")
        )
        expected.add(filename)

        target = MARKDOWN_DIR / filename
        payload = render_markdown_copy(post)

        fd, tmp_name = tempfile.mkstemp(
            prefix="md-",
            suffix=".tmp",
            dir=str(MARKDOWN_DIR),
        )

        try:
            # newline 必须传真正的 "\n"
            with os.fdopen(
                fd,
                "w",
                encoding="utf-8",
                newline="\n",
            ) as f:
                f.write(payload)
                f.flush()
                os.fsync(f.fileno())

            os.replace(
                tmp_name,
                target,
            )
        finally:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)

    # 只删除自动生成且已从 posts.js 消失的 md。
    # 用户自己手写的 Markdown 不会被删除。
    for path in MARKDOWN_DIR.glob("*.md"):
        if path.name in expected:
            continue

        try:
            head = path.read_text(
                encoding="utf-8",
                errors="ignore",
            )[:512]
        except OSError:
            continue

        if "qingyu_generated: true" in head:
            path.unlink(
                missing_ok=True
            )



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
        (
            i
            for i, item in enumerate(posts)
            if isinstance(item, dict) and item.get("id") == post["id"]
        ),
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
        p
        for p in posts
        if not (
            isinstance(p, dict)
            and str(p.get("id")) == post_id
        )
    ]

    if len(filtered) == len(posts):
        return False

    write_posts(filtered)
    return True


def safe_image_stem(filename: str) -> str:
    stem = Path(filename or "image").stem
    stem = re.sub(
        r"[^0-9A-Za-z\u4e00-\u9fff_-]+",
        "-",
        stem,
    ).strip("-_")
    return stem[:48] or "image"


def save_image(raw: object) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("图片数据必须是 JSON 对象")

    data_url = str(raw.get("dataUrl") or "")
    original_name = str(raw.get("name") or "image")

    match = re.fullmatch(
        r"data:(image/(?:png|jpeg|gif|webp|avif));base64,([A-Za-z0-9+/=\r\n]+)",
        data_url,
        flags=re.IGNORECASE,
    )
    if not match:
        raise ValueError("只支持 PNG/JPEG/GIF/WebP/AVIF 图片")

    mime = match.group(1).lower()
    if mime not in IMAGE_TYPES:
        raise ValueError("不支持的图片格式")

    try:
        binary = base64.b64decode(match.group(2), validate=True)
    except (binascii.Error, ValueError) as e:
        raise ValueError("图片 Base64 数据无效") from e

    if not binary:
        raise ValueError("图片内容为空")

    if len(binary) > MAX_IMAGE_BYTES:
        raise ValueError("单张图片不能超过 8 MB")

    IMAGES_DIR.mkdir(parents=True, exist_ok=True)

    stem = safe_image_stem(original_name)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    ext = IMAGE_TYPES[mime]

    while True:
        token = secrets.token_hex(3)
        filename = f"{stamp}-{stem}-{token}{ext}"
        target = IMAGES_DIR / filename
        if not target.exists():
            break

    fd, tmp_name = tempfile.mkstemp(
        prefix="upload-",
        suffix=".tmp",
        dir=str(IMAGES_DIR),
    )
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(binary)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, target)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)

    return {
        "ok": True,
        "name": filename,
        "url": f"/images/{filename}",
        "size": len(binary),
        "type": mime,
        "localFile": True,
    }


def list_local_media() -> list[dict]:
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)

    reverse_types = {
        ext.lower(): mime
        for mime, ext in IMAGE_TYPES.items()
    }

    items = []

    for path in IMAGES_DIR.iterdir():
        if not path.is_file():
            continue

        ext = path.suffix.lower()
        if ext not in reverse_types:
            continue

        try:
            stat = path.stat()
        except OSError:
            continue

        items.append({
            "id": path.name,
            "name": path.name,
            "url": "/images/" + path.name,
            "size": stat.st_size,
            "type": reverse_types.get(
                ext,
                "application/octet-stream",
            ),
            "mtime": int(stat.st_mtime),
            "localFile": True,
        })

    items.sort(
        key=lambda item: (
            item.get("mtime", 0),
            item.get("name", ""),
        ),
        reverse=True,
    )

    return items


def delete_local_media(media_id: str) -> bool:
    media_id = str(media_id or "").strip()

    if not media_id:
        return False

    if Path(media_id).name != media_id:
        raise ValueError("非法媒体文件名")

    target = (IMAGES_DIR / media_id).resolve()
    images_root = IMAGES_DIR.resolve()

    try:
        target.relative_to(images_root)
    except ValueError as e:
        raise ValueError("非法媒体路径") from e

    if not target.exists() or not target.is_file():
        return False

    allowed_exts = {
        ext.lower()
        for ext in IMAGE_TYPES.values()
    }

    if target.suffix.lower() not in allowed_exts:
        raise ValueError("不允许删除该文件类型")

    target.unlink()
    return True


LOCAL_SETTINGS_FILE = ROOT / "local-settings.json"
LOCAL_SETTINGS_JS = PUBLIC / "local-settings.js"


def normalize_local_settings(raw: object) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("设置数据必须是 JSON 对象")

    site = raw.get("site_info", {})
    profile = raw.get("profile", {})
    nav = raw.get("nav", [])

    if not isinstance(site, dict):
        site = {}

    if not isinstance(profile, dict):
        profile = {}

    if isinstance(nav, str):
        try:
            nav = json.loads(nav or "[]")
        except Exception as e:
            raise ValueError("导航 JSON 格式错误") from e

    if not isinstance(nav, list):
        raise ValueError("导航必须是数组")

    clean_site = {
        "name": str(site.get("name") or ""),
        "desc": str(site.get("desc") or ""),
        "avatar": str(site.get("avatar") or ""),
        "copyright": str(site.get("copyright") or ""),
        "footerText": str(site.get("footerText") or ""),
    }

    clean_profile = {
        "name": str(profile.get("name") or ""),
        "bio": str(profile.get("bio") or ""),
        "avatar": str(profile.get("avatar") or ""),
        "email": str(profile.get("email") or ""),
    }

    clean_nav = []

    for item in nav:
        if not isinstance(item, dict):
            continue

        entry = {
            "text": str(item.get("text") or ""),
            "url": str(item.get("url") or "/"),
        }

        children = item.get("children")
        if isinstance(children, list):
            entry["children"] = [
                {
                    "text": str(child.get("text") or ""),
                    "url": str(child.get("url") or "/"),
                }
                for child in children
                if isinstance(child, dict)
            ]

        clean_nav.append(entry)

    return {
        "site_info": clean_site,
        "profile": clean_profile,
        "nav": clean_nav,
        "moderate_comments": (
            "1"
            if str(raw.get("moderate_comments") or "0") == "1"
            else "0"
        ),
    }


def load_local_settings() -> dict:
    if not LOCAL_SETTINGS_FILE.exists():
        return {
            "site_info": {},
            "profile": {},
            "nav": [],
            "moderate_comments": "0",
        }

    try:
        raw = json.loads(
            LOCAL_SETTINGS_FILE.read_text(
                encoding="utf-8"
            )
        )
    except Exception:
        return {
            "site_info": {},
            "profile": {},
            "nav": [],
            "moderate_comments": "0",
        }

    return normalize_local_settings(raw)


def build_local_settings_js(settings: dict) -> str:
    payload = json.dumps(
        settings,
        ensure_ascii=False,
        indent=2,
    )

    return (
        "/* Qingyu'Blog · Python 本地设置覆盖层（自动生成） */\n"
        "window.BLOG_LOCAL_SETTINGS = "
        + payload
        + ";\n"
        "(function () {\n"
        "  var s = window.BLOG_LOCAL_SETTINGS || {};\n"
        "  var c = window.BLOG_CONFIG = window.BLOG_CONFIG || {};\n"
        "  var site = s.site_info || {};\n"
        "  var profile = s.profile || {};\n"
        "\n"
        "  if (site.name) c.title = site.name;\n"
        "  if (Object.prototype.hasOwnProperty.call(site, 'desc')) c.description = site.desc || '';\n"
        "  if (Object.prototype.hasOwnProperty.call(site, 'avatar')) c.siteAvatar = site.avatar || '';\n"
        "  c.profile = profile;\n"
        "\n"
        "  if (Array.isArray(s.nav)) c.nav = s.nav;\n"
        "\n"
        "  c.footer = Object.assign({}, c.footer || {});\n"
        "  if (Object.prototype.hasOwnProperty.call(site, 'copyright')) c.footer.copyrightName = site.copyright || '';\n"
        "  if (Object.prototype.hasOwnProperty.call(site, 'footerText')) c.footer.decl = site.footerText || '';\n"
        "\n"
        "  c.moderateComments = String(s.moderate_comments || '0') === '1';\n"
        "\n"
        "  try {\n"
        "    if (site.name) document.title = site.name;\n"
        "    var meta = document.querySelector('meta[name=\"description\"]');\n"
        "    if (meta && site.desc) meta.setAttribute('content', site.desc);\n"
        "  } catch (e) {}\n"
        "})();\n"
    )


def write_local_settings_js(settings: dict) -> None:
    PUBLIC.mkdir(parents=True, exist_ok=True)

    payload = build_local_settings_js(settings)

    fd, tmp_name = tempfile.mkstemp(
        prefix="local-settings-",
        suffix=".js.tmp",
        dir=str(PUBLIC),
    )

    try:
        with os.fdopen(
            fd,
            "w",
            encoding="utf-8",
            newline="\n",
        ) as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())

        os.replace(
            tmp_name,
            LOCAL_SETTINGS_JS,
        )
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def write_local_settings(raw: object) -> dict:
    settings = normalize_local_settings(raw)

    fd, tmp_name = tempfile.mkstemp(
        prefix="local-settings-",
        suffix=".json.tmp",
        dir=str(ROOT),
    )

    try:
        with os.fdopen(
            fd,
            "w",
            encoding="utf-8",
            newline="\n",
        ) as f:
            json.dump(
                settings,
                f,
                ensure_ascii=False,
                indent=2,
            )
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())

        os.replace(
            tmp_name,
            LOCAL_SETTINGS_FILE,
        )
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)

    write_local_settings_js(settings)
    return settings


def ensure_local_settings_js() -> None:
    try:
        write_local_settings_js(
            load_local_settings()
        )
    except Exception as e:
        print(
            "[Qingyu] local-settings.js warning:",
            e,
        )


class QingyuLocalHandler(SimpleHTTPRequestHandler):
    server_version = "QingyuLocal/1.2"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(PUBLIC), **kwargs)

    def log_message(self, fmt, *args):
        print("[Qingyu]", fmt % args)

    def end_headers(self):
        # 开发模式下避免 admin.js/posts.js 被旧缓存干扰
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def _json(self, status: int, data: dict) -> None:
        body = json.dumps(
            data,
            ensure_ascii=False,
        ).encode("utf-8")

        self.send_response(status)
        self.send_header(
            "Content-Type",
            "application/json; charset=utf-8",
        )
        self.send_header(
            "Content-Length",
            str(len(body)),
        )
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> object:
        try:
            length = int(
                self.headers.get("Content-Length", "0")
            )
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
            return (
                "post",
                path[len("/local-api/posts/"):],
            )

        if path == "/local-api/images":
            return ("images", None)

        if path == "/local-api/media":
            return ("media", None)

        if path.startswith("/local-api/media/"):
            return (
                "media_item",
                path[len("/local-api/media/"):],
            )

        if path == "/local-api/settings":
            return ("settings", None)

        return (None, None)

    def do_GET(self):
        kind, post_id = self._local_api_parts()

        if kind == "status":
            self._json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "writable": True,
                    "storage": "public/posts.js",
                    "imageUpload": True,
                    "imageStorage": "public/images/",
                    "markdownMirror": True,
                    "markdownStorage": "markdown/",
                    "mediaLibrary": True,
                    "settingsWritable": True,
                },
            )
            return

        if kind == "posts":
            try:
                self._json(
                    HTTPStatus.OK,
                    {
                        "ok": True,
                        "posts": load_posts(),
                        "localFile": True,
                    },
                )
            except Exception as e:
                self._json(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    {
                        "ok": False,
                        "error": str(e),
                    },
                )
            return

        if kind == "post":
            try:
                posts = load_posts()
                found = next(
                    (
                        p
                        for p in posts
                        if isinstance(p, dict)
                        and str(p.get("id")) == post_id
                    ),
                    None,
                )

                if found is None:
                    self._json(
                        HTTPStatus.NOT_FOUND,
                        {
                            "ok": False,
                            "error": "文章不存在",
                        },
                    )
                else:
                    self._json(
                        HTTPStatus.OK,
                        {
                            "ok": True,
                            "post": found,
                            "localFile": True,
                        },
                    )
            except Exception as e:
                self._json(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    {
                        "ok": False,
                        "error": str(e),
                    },
                )
            return

        if kind == "media":
            try:
                self._json(
                    HTTPStatus.OK,
                    {
                        "ok": True,
                        "media": list_local_media(),
                        "localFile": True,
                    },
                )
            except Exception as e:
                self._json(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    {
                        "ok": False,
                        "error": str(e),
                    },
                )
            return

        if kind == "settings":
            try:
                self._json(
                    HTTPStatus.OK,
                    {
                        "ok": True,
                        "settings": load_local_settings(),
                        "localFile": True,
                    },
                )
            except Exception as e:
                self._json(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    {
                        "ok": False,
                        "error": str(e),
                    },
                )
            return

        path = unquote(urlparse(self.path).path)

        if path.startswith("/local-api/"):
            self._json(
                HTTPStatus.NOT_FOUND,
                {
                    "ok": False,
                    "error": "Local API Not Found",
                },
            )
            return

        if path.startswith("/api/"):
            self._json(
                HTTPStatus.NOT_FOUND,
                {
                    "ok": False,
                    "error": (
                        "Cloudflare API is not available "
                        "in local Python mode"
                    ),
                },
            )
            return

        target = (
            PUBLIC / path.lstrip("/")
        ).resolve()

        try:
            target.relative_to(PUBLIC.resolve())
        except ValueError:
            self.send_error(HTTPStatus.FORBIDDEN)
            return

        # SPA fallback: /admin 等不存在的真实文件返回 index.html
        if path != "/" and not target.exists():
            self.path = "/index.html"

        super().do_GET()

    def do_POST(self):
        kind, _ = self._local_api_parts()

        if kind == "media":
            try:
                raw = self._read_json()

                if isinstance(raw, dict):
                    raw = dict(raw)

                    if not raw.get("dataUrl") and raw.get("url"):
                        raw["dataUrl"] = raw.get("url")

                result = save_image(raw)
                result["id"] = result.get("name")

                self._json(
                    HTTPStatus.OK,
                    result,
                )
            except ValueError as e:
                self._json(
                    HTTPStatus.BAD_REQUEST,
                    {
                        "ok": False,
                        "error": str(e),
                    },
                )
            except Exception as e:
                self._json(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    {
                        "ok": False,
                        "error": str(e),
                    },
                )
            return

        if kind == "images":
            try:
                result = save_image(self._read_json())
                self._json(
                    HTTPStatus.OK,
                    result,
                )
            except ValueError as e:
                self._json(
                    HTTPStatus.BAD_REQUEST,
                    {
                        "ok": False,
                        "error": str(e),
                    },
                )
            except Exception as e:
                self._json(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    {
                        "ok": False,
                        "error": str(e),
                    },
                )
            return

        if kind == "posts":
            try:
                post = normalize_post(
                    self._read_json()
                )
                upsert_post(post)
                self._json(
                    HTTPStatus.OK,
                    {
                        "ok": True,
                        "post": post,
                        "localFile": True,
                        "message": (
                            "已写入 public/posts.js"
                        ),
                    },
                )
            except ValueError as e:
                self._json(
                    HTTPStatus.BAD_REQUEST,
                    {
                        "ok": False,
                        "error": str(e),
                    },
                )
            except Exception as e:
                self._json(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    {
                        "ok": False,
                        "error": str(e),
                    },
                )
            return

        self._json(
            HTTPStatus.NOT_FOUND,
            {
                "ok": False,
                "error": "Not Found",
            },
        )

    def do_PUT(self):
        kind, post_id = self._local_api_parts()

        if kind == "settings":
            try:
                settings = write_local_settings(
                    self._read_json()
                )

                self._json(
                    HTTPStatus.OK,
                    {
                        "ok": True,
                        "settings": settings,
                        "localFile": True,
                        "message": "本地博客设置已保存",
                    },
                )
            except ValueError as e:
                self._json(
                    HTTPStatus.BAD_REQUEST,
                    {
                        "ok": False,
                        "error": str(e),
                    },
                )
            except Exception as e:
                self._json(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    {
                        "ok": False,
                        "error": str(e),
                    },
                )
            return

        if kind != "post" or not post_id:
            self._json(
                HTTPStatus.NOT_FOUND,
                {
                    "ok": False,
                    "error": "Not Found",
                },
            )
            return

        try:
            post = normalize_post(
                self._read_json(),
                route_id=post_id,
            )
            upsert_post(post)
            self._json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "post": post,
                    "localFile": True,
                    "message": (
                        "已更新 public/posts.js"
                    ),
                },
            )
        except ValueError as e:
            self._json(
                HTTPStatus.BAD_REQUEST,
                {
                    "ok": False,
                    "error": str(e),
                },
            )
        except Exception as e:
            self._json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {
                    "ok": False,
                    "error": str(e),
                },
            )

    def do_DELETE(self):
        kind, post_id = self._local_api_parts()

        if kind == "media_item" and post_id:
            try:
                removed = delete_local_media(post_id)

                if not removed:
                    self._json(
                        HTTPStatus.NOT_FOUND,
                        {
                            "ok": False,
                            "error": "媒体文件不存在",
                        },
                    )
                    return

                self._json(
                    HTTPStatus.OK,
                    {
                        "ok": True,
                        "id": post_id,
                        "localFile": True,
                        "message": "媒体文件已删除",
                    },
                )
            except ValueError as e:
                self._json(
                    HTTPStatus.BAD_REQUEST,
                    {
                        "ok": False,
                        "error": str(e),
                    },
                )
            except Exception as e:
                self._json(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    {
                        "ok": False,
                        "error": str(e),
                    },
                )
            return

        if kind != "post" or not post_id:
            self._json(
                HTTPStatus.NOT_FOUND,
                {
                    "ok": False,
                    "error": "Not Found",
                },
            )
            return

        try:
            removed = delete_post(post_id)

            if not removed:
                self._json(
                    HTTPStatus.NOT_FOUND,
                    {
                        "ok": False,
                        "error": "文章不存在",
                    },
                )
                return

            self._json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "id": post_id,
                    "localFile": True,
                    "message": (
                        "已从 public/posts.js 删除"
                    ),
                },
            )
        except Exception as e:
            self._json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {
                    "ok": False,
                    "error": str(e),
                },
            )


def main():
    parser = argparse.ArgumentParser(
        description="Qingyu'Blog 本地可写服务器"
    )
    parser.add_argument(
        "port",
        nargs="?",
        type=int,
        default=DEFAULT_PORT,
        help=f"监听端口，默认 {DEFAULT_PORT}",
    )
    parser.add_argument(
        "--host",
        default=DEFAULT_HOST,
        help=f"监听地址，默认 {DEFAULT_HOST}",
    )

    args = parser.parse_args()

    if not PUBLIC.exists():
        raise SystemExit(
            f"找不到 public 目录：{PUBLIC}"
        )

    IMAGES_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    MARKDOWN_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    try:
        current_posts = load_posts()
        sync_markdown_files(current_posts)
        print(
            f"[Qingyu] Markdown 已同步："
            f"{len(current_posts)} 篇 -> {MARKDOWN_DIR}"
        )
    except Exception as e:
        print("[Qingyu] Markdown startup sync warning:", e)

    ensure_local_settings_js()

    server = ThreadingHTTPServer(
        (args.host, args.port),
        QingyuLocalHandler,
    )

    print("")
    print("Qingyu'Blog 可写本地服务已启动")
    print(
        f"  首页: http://localhost:{args.port}/"
    )
    print(
        f"  后台: http://localhost:{args.port}/admin"
    )
    print(f"  文章: {POSTS_FILE}")
    print(f"  图片: {IMAGES_DIR}")
    print(f"  Markdown: {MARKDOWN_DIR}")
    print(
        "  支持：粘贴 / 拖拽 / 选择图片"
    )
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
