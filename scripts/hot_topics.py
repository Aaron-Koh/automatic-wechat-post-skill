#!/usr/bin/env python3
"""Merge + dedup + score hot-topic candidates from Tavily and Exa MCP results.

Usage:
  python hot_topics.py \\
    --tavily  /tmp/tavily_raw.json \\
    --exa     /tmp/exa_raw.json \\
    --industry "跨境电商" \\
    --count    5 \\
    --dedup-db ~/.wechat_publisher/published_topics.jsonl

Outputs a JSON array of candidates to stdout. Each candidate has:
  topic_hash, title, summary, sources, date, score, score_breakdown, why
"""
import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse


# Authority weights for well-known financial/tax/trade sources.
# Higher = more trustworthy for 财税 topics.
AUTHORITY_DOMAINS = {
    # 政府/官方
    "gov.cn": 1.00,
    "customs.gov.cn": 1.00,
    "chinatax.gov.cn": 1.00,
    "safe.gov.cn": 1.00,
    "mofcom.gov.cn": 1.00,
    "pbc.gov.cn": 1.00,
    # 权威财经媒体
    "yicai.com": 0.85,
    "caixin.com": 0.85,
    "ebrun.com": 0.85,
    "cifnews.com": 0.85,
    "stcn.com": 0.80,
    # 行业/咨询
    "deloitte.com": 0.80,
    "pwc.com": 0.80,
    "kpmg.com": 0.80,
    "ey.com": 0.80,
    # 综合门户
    "36kr.com": 0.70,
    "sina.com.cn": 0.60,
    "sohu.com": 0.55,
}
DEFAULT_AUTHORITY = 0.50


def load_json_permissive(path):
    """Load JSON. If the file is a raw MCP text-content wrapper, try to unwrap it."""
    with open(path) as f:
        raw = f.read().strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    # Unwrap common MCP shapes: {"content":[{"type":"text","text":"...json..."}]}
    if isinstance(data, dict) and isinstance(data.get("content"), list):
        for chunk in data["content"]:
            if isinstance(chunk, dict) and chunk.get("type") == "text":
                try:
                    return json.loads(chunk["text"])
                except Exception:
                    continue
    return data


def extract_results(payload, source_name):
    """Normalize tavily/exa results into a common list of items."""
    if isinstance(payload, list):
        results = payload
    elif isinstance(payload, dict):
        results = (
            payload.get("results")
            or payload.get("data")
            or payload.get("items")
            or []
        )
    else:
        results = []

    out = []
    for r in results:
        if not isinstance(r, dict):
            continue
        title = (r.get("title") or "").strip()
        url = (r.get("url") or "").strip()
        content = (
            r.get("content")
            or r.get("text")
            or r.get("snippet")
            or r.get("summary")
            or ""
        ).strip()
        date = (
            r.get("published_date")
            or r.get("publishedDate")
            or r.get("date")
            or ""
        )
        if not title or not url:
            continue
        out.append(
            {
                "title": title,
                "url": url,
                "content": content[:500],
                "date": date,
                "source": source_name,
            }
        )
    return out


def canonical_url(url):
    """Produce a URL form stable against query-string and trailing-slash noise."""
    try:
        p = urlparse(url)
        host = (p.hostname or "").lower()
        if host.startswith("www."):
            host = host[4:]
        path = p.path.rstrip("/")
        return f"{host}{path}"
    except Exception:
        return url


def registered_domain(url):
    """Crude eTLD+1 extraction — enough for scoring, not for security decisions."""
    try:
        host = (urlparse(url).hostname or "").lower()
        parts = host.split(".")
        if len(parts) >= 3 and parts[-2] in {"gov", "edu", "ac", "co"}:
            return ".".join(parts[-3:])
        return ".".join(parts[-2:]) if len(parts) >= 2 else host
    except Exception:
        return ""


def title_shingles(title, k=3):
    """Char n-grams on punctuation-stripped title (Chinese-friendly)."""
    norm = re.sub(r"[\s\W_]+", "", title)
    if len(norm) < k:
        return {norm} if norm else set()
    return {norm[i : i + k] for i in range(len(norm) - k + 1)}


def jaccard(a, b):
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def cluster_by_title(items, sim_threshold=0.35):
    """Greedy single-link clustering on title shingle Jaccard."""
    clusters = []
    for item in items:
        sh = title_shingles(item["title"])
        placed = False
        for c in clusters:
            if jaccard(sh, c["shingles"]) >= sim_threshold:
                c["reps"].append(item)
                c["shingles"] |= sh
                placed = True
                break
        if not placed:
            clusters.append({"reps": [item], "shingles": sh})
    return clusters


def parse_date(s):
    if not s:
        return None
    s = str(s)
    for fmt in (
        "%Y-%m-%dT%H:%M:%S.%fZ",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
        "%Y/%m/%d",
    ):
        try:
            # Trim to format length so fractional seconds don't break short formats.
            truncated = s[: len(fmt.replace("%f", "000000"))]
            dt = datetime.strptime(truncated, fmt)
            return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
        except Exception:
            continue
    return None


def recency_score(dates):
    """Newest date across cluster → [0, 1]. 0 at 30+ days old."""
    parsed = [d for d in (parse_date(d) for d in dates) if d is not None]
    if not parsed:
        return 0.4  # unknown date — don't kill it, but don't boost
    newest = max(parsed)
    age_days = max(0, (datetime.now(timezone.utc) - newest).days)
    return max(0.0, 1.0 - age_days / 30.0)


def authority_score(urls):
    """Highest-authority source in the cluster."""
    best = 0.0
    for u in urls:
        d = registered_domain(u)
        for key, sc in AUTHORITY_DOMAINS.items():
            if d == key or d.endswith("." + key):
                best = max(best, sc)
    return best if best > 0 else DEFAULT_AUTHORITY


def relevance_score(cluster, industry):
    """How strongly the cluster matches `{industry} 财税` signals."""
    tax_keywords = [
        "财税", "税务", "税", "合规", "政策", "申报", "关税",
        "增值税", "所得税", "VAT", "海关", "跨境", "出口退税",
        "稽查", "罚款", "税改",
    ]
    blob = " ".join(r["title"] + " " + r["content"] for r in cluster["reps"])
    ind_hits = blob.count(industry)
    tax_hits = sum(blob.count(k) for k in tax_keywords)
    # Saturating combination — industry presence and tax signal each cap at 0.5.
    return min(1.0, 0.5 * min(ind_hits, 4) / 4 + 0.5 * min(tax_hits, 8) / 8)


def topic_hash(cluster):
    """Stable hash across minor title edits — dedup key for the history db."""
    normed = "".join(
        sorted(
            {
                "".join(re.findall(r"[一-鿿A-Za-z0-9]+", r["title"]))
                for r in cluster["reps"]
            }
        )
    )
    return hashlib.sha1(normed.encode("utf-8")).hexdigest()[:12]


def load_dedup_db(path):
    if not path:
        return set()
    p = Path(os.path.expanduser(path))
    if not p.exists():
        return set()
    seen = set()
    with p.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
                h = row.get("topic_hash")
                if h:
                    seen.add(h)
            except Exception:
                continue
    return seen


def pick_representative(cluster):
    """Earliest-dated with the longest content wins — acts as the primary source."""
    def key(it):
        d = parse_date(it["date"]) or datetime.min.replace(tzinfo=timezone.utc)
        return (d, -len(it["content"]))

    return sorted(cluster["reps"], key=key)[0]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tavily", required=True)
    ap.add_argument("--exa", required=True)
    ap.add_argument("--industry", required=True)
    ap.add_argument("--count", type=int, default=5)
    ap.add_argument("--dedup-db", default=None)
    args = ap.parse_args()

    tavily = load_json_permissive(args.tavily) if Path(args.tavily).exists() else {}
    exa = load_json_permissive(args.exa) if Path(args.exa).exists() else {}

    items = extract_results(tavily, "tavily") + extract_results(exa, "exa")

    # URL dedup across sources
    seen = set()
    unique = []
    for it in items:
        cu = canonical_url(it["url"])
        if cu in seen:
            continue
        seen.add(cu)
        unique.append(it)

    if not unique:
        print(json.dumps(
            {"error": "no usable results in input files",
             "hint": "check that Tavily/Exa MCP results were saved correctly"},
            ensure_ascii=False,
        ))
        sys.exit(1)

    clusters = cluster_by_title(unique)
    published = load_dedup_db(args.dedup_db)
    fresh = [c for c in clusters if topic_hash(c) not in published]

    if not fresh:
        print(json.dumps(
            {"error": "all candidates already published",
             "hint": "widen date range, change industry, or clear dedup db"},
            ensure_ascii=False,
        ))
        sys.exit(2)

    scored = []
    for c in fresh:
        rep = pick_representative(c)
        rec = recency_score([r["date"] for r in c["reps"]])
        auth = authority_score([r["url"] for r in c["reps"]])
        rel = relevance_score(c, args.industry)
        total = 0.4 * rec + 0.3 * auth + 0.3 * rel
        scored.append({
            "topic_hash": topic_hash(c),
            "title": rep["title"],
            "summary": rep["content"][:240],
            "sources": [r["url"] for r in c["reps"]],
            "date": rep["date"],
            "score": round(total, 3),
            "score_breakdown": {
                "recency": round(rec, 3),
                "authority": round(auth, 3),
                "relevance": round(rel, 3),
            },
            "why": (
                f"新鲜度 {rec:.2f} · 权威度 {auth:.2f} · "
                f"{args.industry}财税相关度 {rel:.2f}"
            ),
        })

    scored.sort(key=lambda x: x["score"], reverse=True)
    print(json.dumps(scored[: args.count], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
