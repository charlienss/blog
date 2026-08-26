#!/usr/bin/env python3
from __future__ import annotations

import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ADMIN = ROOT / "public" / "admin.js"

DATA_BLOCK = r'''  /* ----------------------- 数据访问（兼容云端 / 静态） ----------------------- */
  var _localWritable = null;

  async function localWritable() {
    if (cloudOn()) return false;
    if (location.protocol !== 'http:' && location.protocol !== 'https:') return false;
    if (_localWritable !== null) return _localWritable;
    try {
      var r = await fetch('/local-api/status', { cache: 'no-store' });
      var ct = (r.headers.get('content-type') || '').toLowerCase();
      if (!r.ok || ct.indexOf('application/json') < 0) {
        _localWritable = false;
        return false;
      }
      var d = await r.json();
      _localWritable = !!(d && d.writable === true);
      return _localWritable;
    } catch (e) {
      _localWritable = false;
      return false;
    }
  }

  async function localApi(url, opts) {
    var o = opts || {};
    var headers = Object.assign({ 'Content-Type': 'application/json' }, o.headers || {});
    var r = await fetch(url, Object.assign({}, o, { headers: headers, cache: 'no-store' }));
    var ct = (r.headers.get('content-type') || '').toLowerCase();
    var d = ct.indexOf('application/json') >= 0 ? await r.json() : null;
    if (!r.ok) throw new Error((d && d.error) || ('HTTP ' + r.status));
    return d || { ok: true };
  }

  function syncMemoryPost(post) {
    if (!post || !post.id) return;
    var arr = Array.isArray(window.BLOG_POSTS) ? window.BLOG_POSTS.slice() : [];
    var idx = arr.findIndex(function (p) { return p && p.id === post.id; });
    if (idx >= 0) arr[idx] = post; else arr.unshift(post);
    window.BLOG_POSTS = arr;
    try {
      var drafts = JSON.parse(localStorage.getItem('qingyu.drafts') || '[]');
      if (Array.isArray(drafts)) {
        drafts = drafts.filter(function (p) { return p && p.id !== post.id; });
        localStorage.setItem('qingyu.drafts', JSON.stringify(drafts));
      }
    } catch (e) {}
  }

  function removeMemoryPost(id) {
    var arr = Array.isArray(window.BLOG_POSTS) ? window.BLOG_POSTS : [];
    window.BLOG_POSTS = arr.filter(function (p) { return p && p.id !== id; });
    try {
      var drafts = JSON.parse(localStorage.getItem('qingyu.drafts') || '[]');
      if (Array.isArray(drafts)) {
        drafts = drafts.filter(function (p) { return p && p.id !== id; });
        localStorage.setItem('qingyu.drafts', JSON.stringify(drafts));
      }
    } catch (e) {}
  }

  async function listPosts() {
    if (cloudOn()) {
      var d = await api('api/posts');
      return (d && d.posts) || [];
    }

    if (await localWritable()) {
      var ld = await localApi('/local-api/posts');
      return (ld && ld.posts) || [];
    }

    var base = (window.getStaticPosts ? window.getStaticPosts() : []) || [];
    var drafts = [];
    try { drafts = JSON.parse(localStorage.getItem('qingyu.drafts') || '[]'); } catch (e) {}
    var map = {};
    base.forEach(function (p) { if (p && p.id) map[p.id] = p; });
    drafts.forEach(function (p) { if (p && p.id) map[p.id] = p; });
    return Object.keys(map).map(function (k) { return map[k]; });
  }

  async function getPost(id) {
    if (cloudOn()) {
      try { var d = await api('api/posts/' + encodeURIComponent(id)); return d && d.post; } catch (e) { return null; }
    }

    if (await localWritable()) {
      try {
        var ld = await localApi('/local-api/posts/' + encodeURIComponent(id));
        return ld && ld.post;
      } catch (e) {
        return null;
      }
    }

    var all = await listPosts();
    for (var i = 0; i < all.length; i++) if (all[i].id === id) return all[i];
    return null;
  }

  function saveStaticPost(post) {
    var drafts = [];
    try { drafts = JSON.parse(localStorage.getItem('qingyu.drafts') || '[]'); } catch (e) {}
    var idx = -1;
    for (var i = 0; i < drafts.length; i++) if (drafts[i] && drafts[i].id === post.id) idx = i;
    var item = { id: post.id, title: post.title, date: post.date, tags: post.tags || [], excerpt: post.excerpt || '',
      cover: post.cover || '', category: post.category || '', status: post.status || 'published',
      pinned: !!post.pinned, content: post.content || '' };
    if (idx >= 0) drafts[idx] = item; else drafts.push(item);
    localStorage.setItem('qingyu.drafts', JSON.stringify(drafts));
  }

  async function savePost(post, isNew) {
    if (cloudOn()) {
      if (isNew) return await api('api/posts', { method: 'POST', body: JSON.stringify(post) });
      return await api('api/posts/' + encodeURIComponent(post.id), { method: 'PUT', body: JSON.stringify(post) });
    }

    if (await localWritable()) {
      var url = isNew ? '/local-api/posts' : '/local-api/posts/' + encodeURIComponent(post.id);
      var r = await localApi(url, {
        method: isNew ? 'POST' : 'PUT',
        body: JSON.stringify(post)
      });
      syncMemoryPost((r && r.post) || post);
      return r;
    }

    saveStaticPost(post);
    return { ok: true };
  }

  async function deletePost(id) {
    if (cloudOn()) return await api('api/posts/' + encodeURIComponent(id), { method: 'DELETE' });

    if (await localWritable()) {
      var r = await localApi('/local-api/posts/' + encodeURIComponent(id), { method: 'DELETE' });
      removeMemoryPost(id);
      return r;
    }

    var drafts = [];
    try { drafts = JSON.parse(localStorage.getItem('qingyu.drafts') || '[]'); } catch (e) {}
    drafts = drafts.filter(function (p) { return p.id !== id; });
    localStorage.setItem('qingyu.drafts', JSON.stringify(drafts));
    return { ok: true };
  }

'''


def main():
    if not ADMIN.exists():
        raise SystemExit(f"找不到 {ADMIN}")

    text = ADMIN.read_text(encoding="utf-8")

    pattern = re.compile(
        r"  /\* ----------------------- 数据访问（兼容云端 / 静态） ----------------------- \*/"
        r"[\s\S]*?"
        r"(?=  function downloadPostsJs\(\) \{)"
    )
    text, n1 = pattern.subn(DATA_BLOCK, text, count=1)
    if n1 != 1:
        raise SystemExit("补丁失败：没有找到 admin.js 的数据访问区块。仓库版本可能已变化。")

    old_pin = """        if (cloudOn()) {
          await api('api/posts/' + enc(pid), { method: 'PUT', body: JSON.stringify(post) });
        } else {
          saveStaticPost(post);
          downloadPostsJs();
        }"""
    new_pin = """        var pinResult = await savePost(post, false);
        if (!cloudOn() && !(pinResult && pinResult.localFile)) {
          downloadPostsJs();
        }"""
    if old_pin not in text:
        raise SystemExit("补丁失败：没有找到置顶保存逻辑。仓库版本可能已变化。")
    text = text.replace(old_pin, new_pin, 1)

    old_saved = """        if (cloudOn()) go('/admin/posts'); else {
          toast(t('admin.editor.savedLocal'), 'ok');
        }"""
    new_saved = """        if (cloudOn() || (r && r.localFile)) {
          go('/admin/posts');
        } else {
          toast(t('admin.editor.savedLocal'), 'ok');
        }"""
    if old_saved not in text:
        raise SystemExit("补丁失败：没有找到编辑器保存后的跳转逻辑。仓库版本可能已变化。")
    text = text.replace(old_saved, new_saved, 1)

    backup = ADMIN.with_suffix(".js.bak-before-local-write")
    shutil.copy2(ADMIN, backup)
    ADMIN.write_text(text, encoding="utf-8", newline="\n")

    print("补丁完成：")
    print(f"  已修改: {ADMIN}")
    print(f"  已备份: {backup}")
    print("")
    print("接下来运行：")
    print("  python serve.py")
    print("")
    print("然后访问：")
    print("  http://localhost:8080/admin")


if __name__ == "__main__":
    main()
