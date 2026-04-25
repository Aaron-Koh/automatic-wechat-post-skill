# 微信公众号 HTML 兼容性规则

调样式时踩坑一次就会记一辈子。排查正文排版错乱时查这里。

---

## 核心规则

### 1. 只有 `style=""` 属性会被保留
`class="xxx"` / `id="xxx"` 都会被微信编辑器静默删掉。任何要生效的样式都必须写成 `style="color:#333;..."` 的**内联形式**。

### 2. `<style>` 标签里的 CSS 全部失效
不管是 `<head>` 里的还是 `<body>` 里的 `<style>` 块，微信都会删除整个标签。所以没法用"定义类、批量应用"的套路。

### 3. 尺寸单位要用绝对 px
`rem`、`em`、`%`（宽度除外）、`vh`、`vw` 都可能被忽略或降级。`font-size` 必须 px。

### 4. 部分 CSS 属性会被吞
被吞的典型：
- `position: fixed / sticky / absolute`（会变 static）
- `transform`（失效）
- `animation`（失效，微信不会跑动画）
- `filter`
- CSS 变量 `var(--xxx)`
- `box-shadow` 在部分客户端失效（尤其旧版 Android）
- `::before` / `::after` 伪元素（无法在 HTML 里写）

### 5. 不能用的标签
会被全部剥掉：`<script>`, `<style>`, `<link>`, `<iframe>`, `<embed>`, `<object>`, `<form>`, `<input>`, `<button>`。

### 6. 外部资源
- 图片 `<img src="...">` 的 `src` 必须是**微信域下的 URL**（`https://mmbiz.qpic.cn/...`），通过 `media/uploadimg` 或 `material/add_material` 拿
- 外链字体（Google Fonts 等）引入不进来，字体只能用系统字体

---

## 标签偏好

### `<section>` > `<div>`
微信会把顶层 `<div>` 规范化，`style` 有时丢失。**包裹块用 `<section>`** 更稳。

### 列表
`<ul>` / `<ol>` 要显式给 `padding-left`（不然会贴左边）：
```html
<ul style="padding-left:22px;margin:12px 0;line-height:1.75;">
  <li style="margin:6px 0;color:#333;">...</li>
</ul>
```

### 表格
```html
<table style="border-collapse:collapse;width:100%;margin:14px 0;font-size:14px;">
  <tr>
    <th style="background:#f5f5f5;border:1px solid #e0e0e0;padding:8px;">列头</th>
  </tr>
  <tr>
    <td style="border:1px solid #e0e0e0;padding:8px;">值</td>
  </tr>
</table>
```
每个 `<th>` / `<td>` 都要自带 style，不能在 `<table>` 上一次写完。

### 代码块
```html
<pre style="background:#2d2d2d;color:#eaeaea;padding:14px;
            border-radius:4px;overflow-x:auto;font-size:13px;">
<code style="background:transparent;color:inherit;">...</code>
</pre>
```
注意：`<pre><code>` 里换行要用实际 `\n`，微信保留空白，不要用 `<br>`。

### 链接
```html
<a style="color:#3370ff;text-decoration:none;" href="https://...">文字</a>
```
⚠️ **订阅号的图文正文里，外部链接在手机端不可点**（仅微信内置浏览器的少数路径下可跳）。但 URL 文本依然显示，读者可以手动复制。

---

## 间距经验值

| 元素 | margin | 说明 |
|---|---|---|
| `<h1>` | 28px 0 16px | 标题前后留白多一些 |
| `<h2>` | 24px 0 12px | 二级标题可加 `border-left` 强调 |
| `<h3>` | 20px 0 10px | —— |
| `<p>` | 12px 0 | 段落默认间距 |
| `<blockquote>` | 16px 0 | 引用前后有呼吸 |
| `<img>` 所在 `<p>` | 20px 0 | 图片上下多留白 |

---

## 字号 / 颜色经验值

- 正文：`font-size: 16px; color: #333; line-height: 1.75;`
- 次要文字（引用、摘要）：`color: #666; font-size: 15px;`
- 强调：`font-weight: 700; color: #222;`
- 主题色（链接、强调块）：`#3370ff`（微信蓝）或品牌色
- 背景淡色：`#fafbfc`（引用块）/ `#f5f5f5`（代码行内）

---

## 图片规范

- 正文图：通过 `media/uploadimg` 拿 URL，写成：
  ```html
  <p style="text-align:center;margin:20px 0;">
    <img src="https://mmbiz.qpic.cn/..." style="max-width:100%;display:block;margin:0 auto;" />
  </p>
  ```
- 不要把 base64 data URL 直接写进 HTML —— 超出 content 长度限制，且微信会过滤
- `<img>` 的 `width` / `height` 属性会被改；用 style 里的 `max-width:100%` 最稳

---

## 检查清单（出稿前）

1. 搜 HTML 里是不是还有 `class=` / `id=` —— 有就清掉
2. 搜 `<style>` / `<script>` —— 有就清掉
3. 搜 `__WECHAT_IMG_` —— 有说明正文图 URL 没替换完整，publisher.py 报 warn 时查
4. 检查所有 `<p>` / `<h?>` / `<li>` 是否都有 `style` —— 没写就会继承微信默认样式，大概率丑
5. 检查表格：每个 `<th>` / `<td>` 都有 style？
