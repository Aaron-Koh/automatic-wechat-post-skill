# automatic-wechat-post-skill

财税主题微信公众号文章自动化 Skill：搜索热点、推荐选题、写稿、配图，并创建微信公众号草稿。

这个 Skill 默认服务于跨境电商财税内容，也可以扩展到鞋服工厂、直播电商、制造业、个体户等行业。它面向需要稳定产出政策解读和合规提醒的 B 端内容场景。

## What It Does

- Searches recent tax, finance, compliance, and policy topics with Tavily and Exa MCP.
- Scores and deduplicates candidate topics locally.
- Drafts 1800-2500 Chinese-character WeChat articles for business owners and finance operators.
- Generates one cover image and three in-article illustrations.
- Converts Markdown into WeChat-compatible inline-styled HTML.
- Uploads article images to WeChat and creates a draft in the Official Account backend.
- Records published topic hashes to reduce repeated topics.

## Scope

This Skill is intentionally narrow.

It does:

- Create WeChat Official Account drafts.
- Focus on tax, finance, compliance, policy, customs, VAT, declaration, and related B2B topics.
- Pause for user topic selection before writing and publishing.
- Remind the user to manually review the final draft in WeChat.

It does not:

- Mass-send articles automatically.
- Manage multiple Official Accounts.
- Schedule posts.
- Handle non-tax general content.
- Replace manual fact-checking for policy details.

## Repository Layout

```text
.
├── SKILL.md                         # Skill instructions and end-to-end workflow
├── requirements.txt                 # Python runtime dependencies
├── scripts/
│   ├── hot_topics.py                # Merge, deduplicate, and score search results
│   ├── md_to_wechat_html.py         # Markdown to WeChat-compatible HTML
│   ├── image_gen.py                 # APIMart image generation wrapper
│   ├── cover_compose.py             # WeChat cover crop and title overlay
│   ├── wechat_client.py             # Minimal WeChat Official Account API client
│   └── publisher.py                 # Upload assets and create WeChat draft
├── references/
│   ├── wechat_api.md                # WeChat API notes and error codes
│   ├── wechat_html_rules.md         # WeChat editor HTML compatibility rules
│   └── writing_style.md             # Article structure and tone guide
└── evals/
    └── evals.json                   # Manual eval scenarios
```

## Prerequisites

You need:

- A verified WeChat Official Account with draft API access.
- The current machine's outbound IP added to the WeChat Official Account IP allowlist.
- Tavily MCP and Exa MCP available in your agent environment.
- An APIMart API key for image generation.
- Python 3.10+.

Install Python dependencies:

```bash
pip install -r requirements.txt
```

Configure credentials with environment variables:

```bash
export WECHAT_APP_ID='wx...'
export WECHAT_APP_SECRET='...'
export APIMART_API_KEY='sk-...'
```

Or store them locally:

```bash
mkdir -p ~/.wechat_publisher
chmod 700 ~/.wechat_publisher

cat > ~/.wechat_publisher/credentials.json <<EOF
{
  "wechat_app_id": "wx...",
  "wechat_app_secret": "...",
  "apimart_api_key": "sk-..."
}
EOF

chmod 600 ~/.wechat_publisher/credentials.json
```

Credentials are loaded in this order:

1. Environment variables
2. `~/.wechat_publisher/credentials.json`

## Usage

Install or link this directory as a Skill in your agent environment, then ask for a finance or tax article to be created for WeChat.

Example prompts:

```text
帮我发一篇跨境电商财税的公众号文章
```

```text
写一篇增值税新规解读发到公众号
```

```text
帮我在公众号发一条鞋服工厂税改的文章
```

The Skill will:

1. Identify the target industry.
2. Search recent topics.
3. Present five topic candidates.
4. Wait for the user to choose a topic.
5. Draft the article.
6. Generate images.
7. Create a WeChat draft.
8. Tell the user to review the draft manually before sending.

## Direct Script Usage

Most users should run this through the Skill workflow. The scripts can also be called directly for debugging.

Merge and score search results:

```bash
python scripts/hot_topics.py \
  --tavily /tmp/wechat_post_tavily.json \
  --exa /tmp/wechat_post_exa.json \
  --industry "跨境电商" \
  --count 5 \
  --dedup-db ~/.wechat_publisher/published_topics.jsonl
```

Convert Markdown to WeChat HTML:

```bash
python scripts/md_to_wechat_html.py \
  --input /tmp/wechat_post_article.md \
  --output /tmp/wechat_post_article.html
```

Create a WeChat draft:

```bash
python scripts/publisher.py \
  --article /tmp/wechat_post_article.md \
  --cover /tmp/wechat_post_cover.png \
  --body-dir /tmp/wechat_post_body/ \
  --industry "跨境电商"
```

## Safety Notes

- The WeChat draft API requires a verified account. Unverified subscription accounts usually fail with `errcode=48001`.
- WeChat requires the caller IP to be allowlisted. If not, calls fail with `errcode=40164`.
- The Skill creates drafts only. Final review and mass sending should happen manually in `https://mp.weixin.qq.com`.
- Policy facts, dates, rates, thresholds, and penalty amounts must be checked against original sources before sending.
- Do not commit credentials, token caches, or local agent settings.

## License

No license has been added yet. Add one before accepting external contributions or redistributing the project broadly.
