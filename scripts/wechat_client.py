#!/usr/bin/env python3
"""Minimal WeChat Official Account API client for the publish flow.

Covers the four endpoints the skill actually needs:
  - cgi-bin/token           → access_token (cached on disk)
  - cgi-bin/material/add_material  → permanent image upload (for thumb_media_id)
  - cgi-bin/media/uploadimg → inline body image upload (URL only, no quota)
  - cgi-bin/draft/add       → create a draft article

Credentials lookup order:
  env vars WECHAT_APP_ID / WECHAT_APP_SECRET, then
  ~/.wechat_publisher/credentials.json keys `wechat_app_id` / `wechat_app_secret`.

access_token is cached at ~/.wechat_publisher/token_cache.json and refreshed
when the remaining lifetime dips below a 5-minute safety margin (WeChat's
tokens nominally last 7200s but we don't want to race the expiry).
"""
import json
import os
import sys
import time
from pathlib import Path

try:
    import requests
except ImportError:
    sys.stderr.write("error: requests not installed. Run: pip install requests\n")
    sys.exit(1)


CONFIG_DIR = Path.home() / ".wechat_publisher"
TOKEN_CACHE = CONFIG_DIR / "token_cache.json"
CREDS_FILE = CONFIG_DIR / "credentials.json"

API_ROOT = "https://api.weixin.qq.com"
TOKEN_REFRESH_MARGIN = 300  # seconds; refresh if <5 min left


# WeChat errors we translate into human-friendly hints so users can self-serve.
ERR_HINTS = {
    40001: "access_token 无效，通常是 AppSecret 错或被重置",
    40164: "当前服务器 IP 不在公众号后台的 IP 白名单里（curl ifconfig.me 拿 IP，加到公众号后台）",
    41001: "缺少 access_token 参数",
    48001: "订阅号未认证，draft/add 等接口不可用，请先完成公众号认证",
    45009: "接口调用达到日上限",
    45166: "封面图 thumb_media_id 不合法，必须用 material/add_material 上传的永久素材",
}


class WeChatError(RuntimeError):
    def __init__(self, errcode, errmsg, hint=None):
        self.errcode = errcode
        self.errmsg = errmsg
        self.hint = hint or ERR_HINTS.get(errcode, "")
        msg = f"WeChat API errcode={errcode} errmsg={errmsg!r}"
        if self.hint:
            msg += f" | 提示：{self.hint}"
        super().__init__(msg)


def _check(resp_json):
    """Raise WeChatError on non-zero errcode; pass through on success."""
    if isinstance(resp_json, dict):
        code = resp_json.get("errcode", 0)
        if code and code != 0:
            raise WeChatError(code, resp_json.get("errmsg", ""))
    return resp_json


def _load_credentials():
    app_id = os.environ.get("WECHAT_APP_ID")
    secret = os.environ.get("WECHAT_APP_SECRET")
    if app_id and secret:
        return app_id, secret
    if CREDS_FILE.exists():
        try:
            data = json.loads(CREDS_FILE.read_text())
            app_id = app_id or data.get("wechat_app_id")
            secret = secret or data.get("wechat_app_secret")
        except Exception:
            pass
    if not app_id or not secret:
        raise RuntimeError(
            "WECHAT_APP_ID / WECHAT_APP_SECRET 未配置。\n"
            "配置方式见 SKILL.md 的 First-time Setup 小节。"
        )
    return app_id, secret


class WeChatClient:
    def __init__(self, app_id=None, app_secret=None):
        if app_id is None or app_secret is None:
            app_id, app_secret = _load_credentials()
        self.app_id = app_id
        self.app_secret = app_secret
        CONFIG_DIR.mkdir(exist_ok=True, mode=0o700)

    # ---------- access_token ----------

    def _read_cached_token(self):
        if not TOKEN_CACHE.exists():
            return None
        try:
            data = json.loads(TOKEN_CACHE.read_text())
        except Exception:
            return None
        # Scope by app_id so multiple accounts don't collide.
        if data.get("app_id") != self.app_id:
            return None
        if data.get("expires_at", 0) - time.time() < TOKEN_REFRESH_MARGIN:
            return None
        return data.get("access_token")

    def _write_cached_token(self, token, expires_in):
        data = {
            "app_id": self.app_id,
            "access_token": token,
            "expires_at": time.time() + expires_in,
        }
        TOKEN_CACHE.write_text(json.dumps(data))
        try:
            TOKEN_CACHE.chmod(0o600)
        except Exception:
            pass

    def get_access_token(self, force_refresh=False):
        if not force_refresh:
            cached = self._read_cached_token()
            if cached:
                return cached
        r = requests.get(
            f"{API_ROOT}/cgi-bin/token",
            params={
                "grant_type": "client_credential",
                "appid": self.app_id,
                "secret": self.app_secret,
            },
            timeout=15,
        )
        r.raise_for_status()
        body = r.json()
        _check(body)
        token = body["access_token"]
        self._write_cached_token(token, int(body.get("expires_in", 7200)))
        return token

    # ---------- uploads ----------

    @staticmethod
    def _mime_for(path):
        """Derive mime type from extension — WeChat cares about matching content."""
        ext = Path(path).suffix.lower()
        return {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".gif": "image/gif",
            ".bmp": "image/bmp",
        }.get(ext, "image/png")

    def upload_permanent_image(self, path):
        """Upload a permanent image material → returns {media_id, url}.

        Used for the cover (thumb_media_id). Counts against 图片素材 quota (5000).
        """
        token = self.get_access_token()
        with open(path, "rb") as f:
            files = {"media": (Path(path).name, f, self._mime_for(path))}
            r = requests.post(
                f"{API_ROOT}/cgi-bin/material/add_material",
                params={"access_token": token, "type": "image"},
                files=files,
                timeout=60,
            )
        r.raise_for_status()
        body = r.json()
        _check(body)
        return {"media_id": body["media_id"], "url": body.get("url", "")}

    def upload_article_image(self, path):
        """Upload an inline image for article body → returns a WeChat-hosted URL.

        Does NOT count against quota, but image must be ≤1MB and jpg/png.
        """
        token = self.get_access_token()
        with open(path, "rb") as f:
            files = {"media": (Path(path).name, f, self._mime_for(path))}
            r = requests.post(
                f"{API_ROOT}/cgi-bin/media/uploadimg",
                params={"access_token": token},
                files=files,
                timeout=60,
            )
        r.raise_for_status()
        body = r.json()
        _check(body)
        return body["url"]

    # ---------- draft ----------

    def create_draft(self, article):
        """Create a single-article draft → returns the draft's media_id.

        `article` fields:
          title, author, digest, content (HTML), thumb_media_id,
          content_source_url (optional), need_open_comment (0|1),
          only_fans_can_comment (0|1)

        Title: ≤64 chars. Digest: ≤120 chars. Content: ≤20k chars post-HTML.
        """
        token = self.get_access_token()
        payload = {"articles": [article]}
        r = requests.post(
            f"{API_ROOT}/cgi-bin/draft/add",
            params={"access_token": token},
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json; charset=utf-8"},
            timeout=30,
        )
        r.raise_for_status()
        body = r.json()
        _check(body)
        return body["media_id"]


def _cli():
    """Tiny CLI for ad-hoc testing: `python wechat_client.py token` / `... ping`."""
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["token", "ping"])
    args = ap.parse_args()
    client = WeChatClient()
    if args.action == "token":
        print(client.get_access_token())
    elif args.action == "ping":
        token = client.get_access_token()
        print(f"ok, app_id={client.app_id}, token={token[:12]}…")


if __name__ == "__main__":
    _cli()
