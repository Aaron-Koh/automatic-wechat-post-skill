---
name: automatic-wechat-post
description: 财税主题（默认跨境电商，覆盖其他行业）的微信公众号文章全流程自动化 —— 搜索热点、选题、写稿、配图、直接发布到公众号草稿箱。Use this skill whenever the user asks to auto-publish / auto-generate a tax or finance related article to 微信公众号 (WeChat Official Account), mentions 跨境电商财税 / 税务政策 / 政策解读 / 合规新规, or says things like "发一篇XX财税的公众号文" / "写一条公众号讲讲XX税" / "帮我在公众号发一篇税改解读". Also triggers when the user combines keywords like 财税/税务/政策/关税/申报/合规/税改 with 公众号/发文/发布/草稿. 默认行业为跨境电商，用户可指定其他行业如鞋服工厂/直播电商/制造业。非财税主题不触发此 Skill。
---

# Automatic WeChat Post（财税主题公众号自动发布）

## Overview

端到端完成**财税主题**微信公众号文章的生产与发布：

1. **热点发现** — Tavily + Exa MCP 并行搜索近 30 天财税热点，本地去重
2. **选题** — 呈现 5 个候选，等用户挑 1 个（暂停点 1）
3. **撰稿** — Claude 按 B 端行业老板视角写 1800-2500 字
4. **配图** — APIMart 生成 1 张商务封面图 + 3 张手绘正文插图
5. **发布** — Markdown → 微信 HTML → 上传素材 → 创建草稿
6. **提醒** — 告知用户去后台人工检查后群发（暂停点 2）

**默认行业**：跨境电商（一等公民，优化最精细）
**可扩展**：鞋服工厂、直播电商、制造业、个体户等，只要是财税主题

## When to Use

**触发**（任何满足"财税主题 + 公众号 + 发/写/自动化"的组合都应该触发）：
- "给我发一篇跨境电商财税的公众号文"
- "写一篇增值税新规解读发到公众号"
- "帮我在公众号发一条鞋服工厂税改的文章"
- "自动发一条跨境电商合规公众号"

**不触发**：
- 非财税主题（比如技术、AI Agent、运营技巧等 → 让别的 Skill 或直接让 Claude 处理）
- 用户只要搜索资讯、不要写文（让用户直接用 Tavily/Exa MCP）
- 用户只要写一篇不发布（让用户直接让 Claude 写）

## Prerequisites

第一次使用前必须配置的凭证：

| 凭证 | 来源 | 用途 |
|---|---|---|
| `WECHAT_APP_ID` | 公众号后台 → 开发 → 基本配置 | 获取 access_token |
| `WECHAT_APP_SECRET` | 同上 | 同上 |
| `APIMART_API_KEY` | apimart.ai | 图像生成 API |

同时要确保：
- 订阅号**已完成认证**（未认证订阅号没有草稿箱接口权限）
- 服务器当前 IP 已加到公众号后台的"IP 白名单"
- Tavily MCP 和 Exa MCP 已安装（`claude mcp list` 可见）
- Python 依赖已安装：`pip install -r requirements.txt`（`markdown`、`beautifulsoup4`、`requests`、`Pillow`）

**读取顺序**：环境变量 > `~/.wechat_publisher/credentials.json`

如果缺失任何一项，**停下来引导用户配置**，不要硬着头皮跑。

## Workflow

### Stage 0 — Parse Industry

从用户请求里提取行业。没提就默认"跨境电商"：

- "跨境电商财税" / 没说 → `industry = "跨境电商"`
- "鞋服工厂财税" → `industry = "鞋服工厂"`
- "直播电商税务" → `industry = "直播电商"`
- "给外贸工厂写一篇关于出口退税的" → `industry = "外贸工厂"`

保留变量 `{industry}` 供后续步骤使用。

### Stage 1 — Hot Topic Discovery

**为什么用两个搜索源**：Tavily 擅长新鲜度和新闻聚合，Exa 擅长语义相似搜索，结合能提升覆盖率。两边各拉 15 条，合并去重。

#### 1a. 调用 Tavily MCP

用 Tavily 的 `search` 工具，参数：
- `query`: `"{industry} 财税 政策 新规 合规 申报 处罚"`（按 industry 动态替换）
- `time_range`: `"month"`（近 30 天）
- `max_results`: `15`
- `search_depth`: `"advanced"`（拿更完整摘要）

把返回的 JSON 保存到 `/tmp/wechat_post_tavily.json`。

#### 1b. 调用 Exa MCP（并行）

用 Exa 的 `web_search_exa` 工具，参数：
- `query`: `"{industry} 财税 税务 政策解读 合规"`
- `num_results`: `15`

保存到 `/tmp/wechat_post_exa.json`。

**注意**：MCP 工具名在不同版本可能略有不同。如果上述工具名不匹配，用实际可用的 Tavily/Exa 搜索工具。

#### 1c. 合并、去重、评分

```bash
python scripts/hot_topics.py \
  --tavily /tmp/wechat_post_tavily.json \
  --exa /tmp/wechat_post_exa.json \
  --industry "{industry}" \
  --count 5 \
  --dedup-db ~/.wechat_publisher/published_topics.jsonl
```

脚本输出 5 个候选（JSON）到 stdout，每个候选包含：`title`, `summary`, `sources`, `date`, `score`, `why`。

### Stage 2 — Topic Selection（暂停点 1）

把 5 个候选展示给用户，格式：

```
## 候选 1: {title}
- 来源: {主要来源域名} + N 个相关
- 时间: {最新时间}
- 摘要: {3 行内}
- 推荐理由: {为什么值得写}

## 候选 2: ...
```

**等用户回复**挑哪一个（"选 1" / "候选 3" / "1 和 3 合并"）。如果用户全不满意，问 TA 要不要重新搜（可以建议调整关键词），或者放弃这次发文。

选定后，把选中的候选保存到 `/tmp/wechat_post_topic.json`。

### Stage 3 — Writing

**必读**: `references/writing_style.md`（完整读者画像 + 结构模板 + 口吻范例）

核心要求：
- **字数**：1800-2500 字
- **结构**：事件背景 → 政策原文要点 → 对{industry}从业者的实际影响 → 合规动作清单
- **口吻**：B 端{industry}老板视角，讲"这事对你钱包什么影响，你要立刻做什么"
- **事实核查（最重要）**：每条政策引用必须追溯到原链接，绝不编造条款编号/税率/日期/罚款金额
- **引用样式**：文末附"参考资料"列表，所有 URL 来自 Stage 1 的搜索结果

把 Markdown 保存到 `/tmp/wechat_post_article.md`。第一行是 `# {标题}`，后面正文。

### Stage 4 — Image Generation

#### 4a. 封面图（商务专业风）

Prompt 模板（英文）：
```
A professional business editorial illustration for a finance/tax article about {topic brief}.
Flat vector style, modern corporate aesthetic, muted blue and gold palette,
global trade/compliance motifs (container ships, documents, currencies, world map outlines),
clean composition with negative space on the right for overlay text,
no cartoon characters, no realism, no cyberpunk, 16:9 landscape.
```

调用：
```bash
python scripts/image_gen.py cover \
  --prompt "..." \
  --output /tmp/wechat_post_cover_raw.png \
  --resolution 2K
```

然后用 PIL 叠标题 + 裁切到 900×383：
```bash
python scripts/cover_compose.py \
  --input /tmp/wechat_post_cover_raw.png \
  --title "{文章标题}" \
  --output /tmp/wechat_post_cover.png
```

#### 4b. 正文 3 张插图（手绘漫画风，沿用同级 Skill 风格）

Prompt 模板：由 Claude 读文章前 30% / 中间 / 后 30% 的核心内容，各生成一个英文 prompt，格式：

```
{scene description}, hand-drawn comic style, warm and friendly atmosphere,
storytelling feel, {composition hint}, clean background, clear subject,
no realism, no cyberpunk, Chinese text if any text appears
```

调用：
```bash
python scripts/image_gen.py body \
  --article /tmp/wechat_post_article.md \
  --output-dir /tmp/wechat_post_body/
```

产出 `/tmp/wechat_post_body/img_1.png`, `img_2.png`, `img_3.png`。

**注意**：封面和正文是**两种不同风格**。封面是商务编辑插画（吸引点击），正文是手绘漫画（缓解阅读压力）。这种对比是有意的。

### Stage 5 — Publish to WeChat Draft

```bash
python scripts/publisher.py \
  --article /tmp/wechat_post_article.md \
  --cover /tmp/wechat_post_cover.png \
  --body-dir /tmp/wechat_post_body/ \
  --industry "{industry}"
```

内部流程（详见 `references/wechat_api.md`）：
1. `md_to_wechat_html.py` 转 Markdown → 微信兼容 HTML（全内联样式）
2. 在 HTML 里找到 3 个 `{{IMAGE_PLACEHOLDER_N}}`，通过 `media/uploadimg` 上传正文图拿 URL，替换回 HTML
3. 封面图通过 `material/add_material` 上传为**永久素材**，拿 `thumb_media_id`
4. 调 `draft/add` 创建草稿，拿 `media_id`
5. 登记到 `~/.wechat_publisher/published_topics.jsonl`（含 topic_hash + industry + draft_media_id + 时间戳）

输出 draft_media_id 到 stdout。

### Stage 6 — Notify（暂停点 2）

展示给用户：

```
✅ 草稿已创建成功

📝 标题：{文章标题}
🏷️  行业：{industry}
📊 字数：{字数}
🖼️  配图：1 张封面 + 3 张正文
🔖 草稿 media_id：{media_id}

👉 下一步：
1. 登录 https://mp.weixin.qq.com
2. 内容管理 → 草稿箱 → 找到这篇草稿
3. 检查标题、封面、正文、引用是否准确
4. 确认无误后群发

⚠️ 事实核查提醒：本文引用了 X 条政策/数据，建议对照原链接再核对一遍。
```

## First-time Setup

如果 `WECHAT_APP_ID` / `WECHAT_APP_SECRET` / `APIMART_API_KEY` 任何一项缺失，引导用户：

1. **公众号凭证**：登录 `https://mp.weixin.qq.com` → 开发 → 基本配置
   - 复制 **AppID**
   - 点"生成" AppSecret（只显示一次，立刻保存）
   - 在"IP 白名单"里加当前服务器的出口 IP（可以 `curl ifconfig.me` 拿到）

2. **APIMart 凭证**：`https://apimart.ai` 注册后拿 API Key

3. **保存方式**（二选一）：

   ```bash
   # 方式 A：环境变量（推荐）
   export WECHAT_APP_ID='wx...'
   export WECHAT_APP_SECRET='...'
   export APIMART_API_KEY='sk-...'
   ```

   ```bash
   # 方式 B：配置文件
   mkdir -p ~/.wechat_publisher
   cat > ~/.wechat_publisher/credentials.json <<EOF
   {
     "wechat_app_id": "wx...",
     "wechat_app_secret": "...",
     "apimart_api_key": "sk-..."
   }
   EOF
   chmod 600 ~/.wechat_publisher/credentials.json
   ```

## References

遇到细节问题时查对应文档：

- **`references/writing_style.md`** — 读者画像、文章结构模板、口吻范例、常见写作陷阱
- **`references/wechat_api.md`** — 所有微信 API 接口说明 + 错误码速查
- **`references/wechat_html_rules.md`** — 微信编辑器 HTML 兼容性（哪些标签/样式能用）

## Common Pitfalls

| 症状 | 原因 | 解决 |
|---|---|---|
| `errcode: 48001` | 订阅号未认证 | 去后台完成认证（需要公司资质） |
| `errcode: 40164` | IP 不在白名单 | `curl ifconfig.me` 拿 IP，加到后台白名单 |
| `errcode: 40001` | access_token 过期 | 正常，缓存层会自动刷新；若频繁出现检查 AppSecret 是否正确 |
| 草稿排版错乱 | HTML 用了 class/id | 全改成 `style=""` 内联样式 |
| 封面不显示 | `thumb_media_id` 用的是临时素材 | 必须用 `material/add_material` 上传的永久素材 |
| 话题"又"写了一遍 | 去重库未命中 | 检查 `published_topics.jsonl` 字段；可能是标题改动大但内容同 |
| 引用编造 | Claude 凭印象写政策条款 | **严禁**，每条必须来自 Stage 1 搜索结果的原链接；不确定宁可删 |

## Non-goals（这个 Skill 不做的事）

- 群发：只创建草稿，群发由用户在后台手动点
- 多号管理：一次只对一个已配好凭证的公众号发
- 定时触发：靠用户主动发起
- 非财税主题：明确拒绝，不硬写

---

_调整 prompt 模板、评分公式、口吻范例等参数，请去对应脚本的常量区或 references/ 文档，不要动 SKILL.md 的主流程。_
