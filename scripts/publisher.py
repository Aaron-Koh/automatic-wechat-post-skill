#!/usr/bin/env python3
"""Orchestrate the end-to-end publish:

  1. parse title + digest from the markdown
  2. convert MD → WeChat HTML (via md_to_wechat_html.py)
  3. upload 3 body images via cgi-bin/media/uploadimg, splice URLs back
  4. upload cover via cgi-bin/material/add_material → thumb_media_id
  5. create a draft via cgi-bin/draft/add → returns draft media_id
  6. append a row to ~/.wechat_publisher/published_topics.jsonl
  7. print a summary for the user

Each step fails loud: if anything goes wrong mid-flight we stop before
creating a half-baked draft.

Usage:
  python publisher.py \\
    --article  /tmp/wechat_post_article.md \\
    --cover    /tmp/wechat_post_cover.png \\
    --body-dir /tmp/wechat_post_body/ \\
    --industry "跨境电商" \\
    [--author "财税洞察"] \\
    [--content-source-url "https://..."]
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPTS_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPTS_DIR))

from wechat_client import WeChatClient, WeChatError  # noqa: E402


DEDUP_DB = Path.home() / ".wechat_publisher" / "published_topics.jsonl"


def extract_title(md_text):
    """First `# ` line, stripped."""
    for line in md_text.splitlines():
        line = line.rstrip()
        if line.startswith("# ") and not line.startswith("## "):
            return line[2:].strip()
    raise ValueError(
        "article has no top-level `# Title` line; add one as the first heading"
    )


def extract_digest(md_text, max_len=120):
    """Use the first meaningful paragraph for the article digest.

    Skips headings, quotes, lists, code fences, image markers — those rarely
    make sense as a preview snippet on the feed.
    """
    lines = md_text.splitlines()
    buf = []
    started = False
    for line in lines:
        s = line.strip()
        if not s:
            if started:
                break
            continue
        if s.startswith("#") or s.startswith(">") or s.startswith("- ") or s.startswith("* "):
            continue
        if s.startswith("{{IMG:") or s.startswith("```") or re.match(r"^\d+\.\s", s):
            continue
        started = True
        buf.append(s)
    digest = "".join(buf)
    # Light markdown cleanup for readability in the feed preview.
    digest = re.sub(r"\*\*([^*]+)\*\*", r"\1", digest)
    digest = re.sub(r"\*([^*]+)\*", r"\1", digest)
    digest = re.sub(r"`([^`]+)`", r"\1", digest)
    digest = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", digest)
    if len(digest) > max_len:
        digest = digest[: max_len - 1] + "…"
    return digest


def convert_md_to_html(md_path):
    """Shell out to md_to_wechat_html.py so its dependencies stay isolated."""
    r = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "md_to_wechat_html.py"),
         "--input", str(md_path)],
        check=True, capture_output=True, text=True,
    )
    return r.stdout


def compress_for_body_upload(src_path, max_bytes=900_000, max_width=1080):
    """Shrink an image to fit under media/uploadimg's 1 MB cap.

    APIMart's "1K" output is ~1.7-2.2 MB PNG — too big for WeChat. We resize to
    at most 1080px wide (plenty for mobile retina) and re-encode as optimized
    PNG. If that's still over the cap, we fall back to JPEG at quality 85.
    Returns the path to use for upload (may be a new temp file).
    """
    from PIL import Image

    src = Path(src_path)
    if src.stat().st_size <= max_bytes:
        return str(src)

    im = Image.open(src)
    if im.width > max_width:
        ratio = max_width / im.width
        im = im.resize((max_width, int(im.height * ratio)), Image.LANCZOS)

    out_png = src.with_name(src.stem + "_c.png")
    im.save(out_png, "PNG", optimize=True)
    if out_png.stat().st_size <= max_bytes:
        return str(out_png)

    # PNG optimize not enough for photographic content → JPEG.
    out_jpg = src.with_name(src.stem + "_c.jpg")
    im.convert("RGB").save(out_jpg, "JPEG", quality=85, optimize=True)
    return str(out_jpg)


def upload_body_images(client, body_dir):
    """Upload img_1.png, img_2.png, img_3.png — returns {1: url, ...} for found."""
    urls = {}
    body_dir = Path(body_dir)
    for i in (1, 2, 3):
        path = body_dir / f"img_{i}.png"
        if not path.exists():
            sys.stderr.write(f"  warn: {path.name} not found, skipping position {i}\n")
            continue
        upload_path = compress_for_body_upload(str(path))
        if upload_path != str(path):
            orig_kb = path.stat().st_size / 1024
            new_kb = Path(upload_path).stat().st_size / 1024
            sys.stderr.write(
                f"  compressed img_{i}: {orig_kb:.0f} KB → {new_kb:.0f} KB "
                f"({Path(upload_path).suffix})\n"
            )
        url = client.upload_article_image(upload_path)
        sys.stderr.write(f"  body img_{i} uploaded → {url}\n")
        urls[i] = url
    return urls


def substitute_image_tokens(html, url_map):
    """Replace `__WECHAT_IMG_N__` placeholders with uploaded URLs."""
    for i, url in url_map.items():
        html = html.replace(f"__WECHAT_IMG_{i}__", url)
    # Any unsubstituted tokens mean the image was missing; drop the src so the
    # editor doesn't show a broken-image icon.
    html = re.sub(r'src="__WECHAT_IMG_\d+__"', 'src=""', html)
    return html


def topic_hash_for(title):
    """Same hashing logic as hot_topics.py so dedup is consistent."""
    norm = "".join(re.findall(r"[一-鿿A-Za-z0-9]+", title))
    return hashlib.sha1(norm.encode("utf-8")).hexdigest()[:12]


def append_dedup(industry, title, topic_hash, draft_media_id, sources=None):
    DEDUP_DB.parent.mkdir(exist_ok=True, mode=0o700)
    entry = {
        "date": datetime.now(timezone.utc).isoformat(),
        "industry": industry,
        "title": title,
        "topic_hash": topic_hash,
        "draft_media_id": draft_media_id,
        "sources": sources or [],
    }
    with DEDUP_DB.open("a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    try:
        DEDUP_DB.chmod(0o600)
    except Exception:
        pass


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--article", required=True)
    ap.add_argument("--cover", required=True)
    ap.add_argument("--body-dir", required=True)
    ap.add_argument("--industry", required=True)
    ap.add_argument("--author", default="")
    ap.add_argument("--content-source-url", default="",
                    help="Optional 'read the original' URL shown at article bottom")
    ap.add_argument("--sources-json", default=None,
                    help="Optional JSON list of source URLs (goes into dedup DB)")
    args = ap.parse_args()

    md_path = Path(args.article)
    md_text = md_path.read_text(encoding="utf-8")
    title = extract_title(md_text)
    digest = extract_digest(md_text)

    sys.stderr.write(f"→ title  : {title}\n")
    sys.stderr.write(f"→ digest : {digest}\n")

    # Step 2: MD → HTML
    sys.stderr.write("→ converting markdown to WeChat HTML…\n")
    html = convert_md_to_html(md_path)

    client = WeChatClient()

    # Step 3: upload body images, splice URLs into HTML
    sys.stderr.write("→ uploading body images…\n")
    body_urls = upload_body_images(client, args.body_dir)
    html = substitute_image_tokens(html, body_urls)

    # Step 4: upload cover as permanent material → thumb_media_id
    sys.stderr.write("→ uploading cover as permanent material…\n")
    cover_info = client.upload_permanent_image(args.cover)
    thumb_media_id = cover_info["media_id"]
    sys.stderr.write(f"  thumb_media_id = {thumb_media_id}\n")

    # Step 5: create draft
    article = {
        "article_type": "news",
        "title": title[:64],   # WeChat hard-limits title to 64 chars
        "author": args.author,
        "digest": digest,
        "content": html,
        "content_source_url": args.content_source_url,
        "thumb_media_id": thumb_media_id,
        "need_open_comment": 0,
        "only_fans_can_comment": 0,
    }
    sys.stderr.write("→ creating draft…\n")
    draft_media_id = client.create_draft(article)
    sys.stderr.write(f"  draft media_id = {draft_media_id}\n")

    # Step 6: dedup register
    sources = []
    if args.sources_json:
        try:
            sources = json.loads(Path(args.sources_json).read_text())
        except Exception:
            pass
    append_dedup(args.industry, title, topic_hash_for(title), draft_media_id, sources)

    # Step 7: summary for the user
    word_count = len(re.sub(r"\s+", "", md_text))
    summary = {
        "status": "ok",
        "title": title,
        "industry": args.industry,
        "word_count": word_count,
        "body_images_uploaded": len(body_urls),
        "draft_media_id": draft_media_id,
        "next_step": "登录 https://mp.weixin.qq.com → 内容管理 → 草稿箱，检查后群发",
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except WeChatError as e:
        sys.stderr.write(f"\nFAILED: {e}\n")
        sys.exit(10)
    except Exception as e:
        sys.stderr.write(f"\nFAILED: {type(e).__name__}: {e}\n")
        sys.exit(11)
