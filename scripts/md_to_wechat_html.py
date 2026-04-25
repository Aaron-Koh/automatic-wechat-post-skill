#!/usr/bin/env python3
"""Convert a Markdown article to WeChat-Official-Account-compatible HTML.

WeChat's editor accepts a subset of HTML and strips anything it dislikes:
  - `class` / `id` attributes are silently removed
  - `<style>`, `<script>`, `<link>`, `<iframe>` are stripped
  - Only inline `style="..."` survives, and even then some properties (relative
    units, `position`, CSS variables) get ignored. So every visible element
    must carry an explicit inline style with absolute px sizes.

Image handling:
  The markdown input may contain placeholder markers `{{IMG:1}}` / `{{IMG:2}}`
  / `{{IMG:3}}` on their own lines. This script converts them to
  `<img src="__WECHAT_IMG_1__" ...>` etc.; publisher.py later substitutes
  those tokens with the URLs returned by `media/uploadimg`.

Usage:
  python md_to_wechat_html.py --input article.md --output article.html
  python md_to_wechat_html.py --input article.md   # HTML to stdout
"""
import argparse
import re
import sys

try:
    import markdown as md_lib
except ImportError:
    sys.stderr.write(
        "error: `markdown` package not installed. Run: pip install markdown\n"
    )
    sys.exit(1)

try:
    from bs4 import BeautifulSoup
except ImportError:
    sys.stderr.write(
        "error: `beautifulsoup4` package not installed. Run: pip install beautifulsoup4\n"
    )
    sys.exit(1)


# Inline styles — tuned for a serious B2B finance/tax article on WeChat.
# Every visible tag must carry inline style; classes are stripped by WeChat.
STYLE = {
    "section": "margin: 0; padding: 0;",
    "h1": (
        "font-size:22px;color:#222;font-weight:700;"
        "margin:28px 0 16px;line-height:1.4;letter-spacing:0.5px;"
    ),
    "h2": (
        "font-size:19px;color:#222;font-weight:700;"
        "margin:24px 0 12px;line-height:1.45;"
        "border-left:3px solid #3370ff;padding-left:10px;"
    ),
    "h3": (
        "font-size:17px;color:#333;font-weight:700;"
        "margin:20px 0 10px;line-height:1.5;"
    ),
    "h4": (
        "font-size:16px;color:#333;font-weight:700;"
        "margin:16px 0 8px;line-height:1.5;"
    ),
    "p": (
        "font-size:16px;line-height:1.75;color:#333;"
        "margin:12px 0;text-align:justify;letter-spacing:0.3px;"
    ),
    "blockquote": (
        "border-left:3px solid #3370ff;background:#fafbfc;"
        "padding:8px 14px;margin:16px 0;color:#555;"
        "font-size:15px;line-height:1.7;"
    ),
    "ul": "padding-left:22px;margin:12px 0;line-height:1.75;",
    "ol": "padding-left:22px;margin:12px 0;line-height:1.75;",
    "li": "margin:6px 0;color:#333;font-size:16px;line-height:1.75;",
    "strong": "color:#222;font-weight:700;",
    "em": "color:#333;font-style:italic;",
    "a": "color:#3370ff;text-decoration:none;",
    "hr": "border:none;border-top:1px solid #eaeaea;margin:24px 0;",
    "code_inline": (
        "background:#f5f5f5;padding:2px 5px;border-radius:3px;"
        "font-family:Menlo,Monaco,Consolas,monospace;"
        "font-size:14px;color:#c7254e;"
    ),
    "pre": (
        "background:#2d2d2d;color:#eaeaea;padding:14px;border-radius:4px;"
        "overflow-x:auto;font-family:Menlo,Monaco,Consolas,monospace;"
        "font-size:13px;line-height:1.6;margin:16px 0;"
    ),
    "pre_code": "background:transparent;color:inherit;padding:0;font-size:13px;",
    "table": (
        "border-collapse:collapse;width:100%;margin:14px 0;"
        "font-size:14px;line-height:1.6;"
    ),
    "th": (
        "background:#f5f5f5;border:1px solid #e0e0e0;"
        "padding:8px 10px;text-align:left;font-weight:700;"
    ),
    "td": "border:1px solid #e0e0e0;padding:8px 10px;color:#333;",
    "img_wrapper": "text-align:center;margin:20px 0;",
    "img": "max-width:100%;display:block;margin:0 auto;border-radius:4px;",
}

# Tags we refuse to emit — stripped for safety and WeChat compatibility.
BANNED_TAGS = {"script", "style", "iframe", "embed", "object", "link", "meta"}


def flatten_list_items(soup):
    """Strip <p> wrappers that python-markdown inserts inside <li> for "loose lists".

    Markdown like:
        - **Header**
          indented continuation
    produces <li><p>content</p></li>. The inner <p> carries its normal
    margins, and WeChat's editor renders those margins as visible blank
    space that still shows the list bullet — users see phantom bullets on
    what look like empty lines above and below the actual item text.

    Fix: unwrap the paragraphs. For single-<p> items, replace with the
    paragraph's children. For multi-<p> items, join with <br/> so the
    visual line break is preserved without inheriting <p> margins.
    """
    from bs4 import NavigableString

    for li in soup.find_all("li"):
        direct_ps = [c for c in li.children if getattr(c, "name", None) == "p"]
        if not direct_ps:
            continue
        # For multi-paragraph items, insert a <br/> between paragraphs before
        # unwrapping so the visual line break survives.
        for i, p in enumerate(direct_ps):
            if i > 0:
                p.insert_before(soup.new_tag("br"))
            p.unwrap()
        # Clean up stray whitespace-only text nodes left over.
        for child in list(li.children):
            if isinstance(child, NavigableString) and not child.strip():
                child.extract()


def apply_inline_style(soup):
    """Walk the tree and apply STYLE to every element, dropping banned tags."""
    for tag in soup.find_all(True):
        if tag.name in BANNED_TAGS:
            tag.decompose()
            continue

        # Strip class/id — WeChat ignores them and they add noise.
        if tag.attrs is None:
            tag.attrs = {}
        tag.attrs.pop("class", None)
        tag.attrs.pop("id", None)

        name = tag.name
        if name in ("h1", "h2", "h3", "h4", "p", "ul", "ol", "li",
                    "strong", "em", "a", "hr", "blockquote",
                    "table", "th", "td"):
            tag["style"] = STYLE[name]
        elif name == "pre":
            tag["style"] = STYLE["pre"]
            # Also restyle any <code> inside — it lives in the dark theme.
            for code in tag.find_all("code"):
                code["style"] = STYLE["pre_code"]
        elif name == "code":
            # Inline code only; pre>code already handled above.
            if tag.parent and tag.parent.name != "pre":
                tag["style"] = STYLE["code_inline"]


def wrap_images(html):
    """Convert `{{IMG:N}}` placeholder tokens into WeChat-safe image blocks.

    The tokens appear in the source Markdown on their own lines; after MD parsing
    they end up wrapped in `<p>{{IMG:N}}</p>`. We detect both shapes and emit a
    centered <p> wrapper with a percentage-width <img> using a substitution token
    (`__WECHAT_IMG_N__`) that publisher.py will replace post-upload.
    """
    # <p>{{IMG:N}}</p> → styled <p><img></p>
    html = re.sub(
        r"<p[^>]*>\s*\{\{IMG:(\d+)\}\}\s*</p>",
        lambda m: (
            f'<p style="{STYLE["img_wrapper"]}">'
            f'<img src="__WECHAT_IMG_{m.group(1)}__" '
            f'style="{STYLE["img"]}" />'
            f"</p>"
        ),
        html,
    )
    # Bare {{IMG:N}} anywhere else — same treatment.
    html = re.sub(
        r"\{\{IMG:(\d+)\}\}",
        lambda m: (
            f'<p style="{STYLE["img_wrapper"]}">'
            f'<img src="__WECHAT_IMG_{m.group(1)}__" '
            f'style="{STYLE["img"]}" />'
            f"</p>"
        ),
        html,
    )
    return html


def ensure_section_wrapper(html):
    """Wrap the whole body in a <section> with base typography so WeChat's
    outer <p> defaults don't override ours. Also avoids WeChat's tendency
    to flatten top-level <div>s."""
    outer_style = (
        "font-size:16px;color:#333;line-height:1.75;"
        "font-family:-apple-system,BlinkMacSystemFont,'PingFang SC',"
        "'Helvetica Neue',Arial,sans-serif;"
    )
    return f'<section style="{outer_style}">{html}</section>'


def strip_leading_h1(md_text):
    """Drop the first top-level `# Title` line.

    WeChat's draft API renders the title separately from the HTML body, so
    keeping `# Title` in the markdown body would produce a duplicate title
    at the top of the rendered article.
    """
    lines = md_text.splitlines()
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        if line.startswith("# ") and not line.startswith("## "):
            # Drop this line and any blank lines immediately after it.
            j = i + 1
            while j < len(lines) and not lines[j].strip():
                j += 1
            return "\n".join(lines[:i] + lines[j:])
        return md_text
    return md_text


def convert(md_text):
    md_text = strip_leading_h1(md_text)
    # Extensions: fenced_code (```), tables, sane_lists (cleaner nested lists)
    html = md_lib.markdown(
        md_text,
        extensions=["fenced_code", "tables", "sane_lists", "nl2br"],
        output_format="html5",
    )
    soup = BeautifulSoup(html, "html.parser")
    flatten_list_items(soup)
    apply_inline_style(soup)
    html = str(soup)
    html = wrap_images(html)
    html = ensure_section_wrapper(html)
    return html


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--input", required=True, help="Path to Markdown file")
    ap.add_argument("--output", default=None, help="Output HTML path; stdout if omitted")
    args = ap.parse_args()

    with open(args.input, encoding="utf-8") as f:
        md_text = f.read()

    html = convert(md_text)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(html)
    else:
        sys.stdout.write(html)


if __name__ == "__main__":
    main()
