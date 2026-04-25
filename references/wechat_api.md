# 微信公众号 API 参考

排查 publish 流程中的 API 问题时看这里。

---

## 接口清单（本 Skill 用到的）

| 接口 | 方法 | 路径 | 用途 |
|---|---|---|---|
| token | GET | `/cgi-bin/token` | 拿 access_token |
| material/add_material | POST (multipart) | `/cgi-bin/material/add_material?type=image` | 上传**永久图片素材**（封面用） |
| media/uploadimg | POST (multipart) | `/cgi-bin/media/uploadimg` | 上传**正文图**，返回可直接嵌入 HTML 的 URL |
| draft/add | POST (JSON) | `/cgi-bin/draft/add` | 创建草稿 |

所有接口 base URL：`https://api.weixin.qq.com`。

---

## access_token 规则

- 调 `/cgi-bin/token?grant_type=client_credential&appid=APPID&secret=APPSECRET`
- 返回 `{"access_token": "...", "expires_in": 7200}`（秒）
- **必须缓存**：生产环境每天限额有限，每次调都刷会被限流
- 本 Skill 缓存路径：`~/.wechat_publisher/token_cache.json`
- 缓存策略：到期前 5 分钟就视为过期刷新（避免临界点请求失败）

**常见坑**：
- 两个系统同时拉 token 会互相覆盖，一个拿到无效的。本 Skill 单进程用，不担心，但生产多节点要加集中缓存（Redis）。
- token 被重置（比如你在后台手动重置 AppSecret）后，旧 token 立即失效。删除 `token_cache.json` 重新调即可。

---

## 草稿结构（draft/add）

POST body：
```json
{
  "articles": [{
    "article_type": "news",
    "title": "...",             // ≤64 字
    "author": "...",             // 可空
    "digest": "...",             // 摘要，≤120 字
    "content": "<html...>",      // 正文 HTML，≤20k 字
    "content_source_url": "",    // "阅读原文"跳转 URL，可空
    "thumb_media_id": "...",     // 封面图，必须是 material/add_material 永久素材
    "need_open_comment": 0,      // 0 = 不开评论
    "only_fans_can_comment": 0
  }]
}
```

成功响应：`{"media_id": "xxxx"}` —— 这是**草稿的 media_id**，不是素材 media_id。要在后台找草稿就靠这个。

---

## 关键规则

### 1. 封面必须是永久素材
`thumb_media_id` 只接受 `material/add_material` 上传的永久素材返回值，**不能用临时素材**（`media/upload`）。用错了会返回 45166。

永久图片素材配额：5000 张/账号。用完要先删旧的（`material/del_material`）。本 Skill 不做自动清理，因为删错代价大；如果确实要清，建议在微信后台的"素材管理"里手动操作。

### 2. 正文图片不占素材配额
`media/uploadimg` 专门给正文图用，**不计入素材库 5000 张上限**，但：
- 单张 ≤1MB
- 格式：JPG / PNG
- 返回的 URL 只能在当前订阅号的图文里用

### 3. HTML 长度上限
`content` 字段字节数 ≤ 20k 字符（官方没明说具体是 20000 字符还是 20KB，保守按字符数算）。一篇 2500 字的中文文章 + 内联样式 HTML，大约在 8k-12k 字符之间，远没到上限，但**不要把 base64 图片塞进 content**（会爆）。

---

## 错误码速查

| errcode | 含义 | 处理 |
|---|---|---|
| 0 | 成功 | —— |
| 40001 | access_token 无效 | AppSecret 错 / 被重置；本地 token 缓存损坏。删除 `token_cache.json` 重试。 |
| 40164 | 调用 IP 不在白名单 | `curl ifconfig.me` 拿 IP，加到后台"开发 → 基本配置 → IP 白名单" |
| 41001 | 缺 access_token | 请求 URL 忘了加 `?access_token=...` |
| 45009 | 超过接口调用频率 | 单日限额超了；等 24 小时或联系微信商务 |
| 45166 | thumb_media_id 不合法 | 用了临时素材；改用 `material/add_material` 上传永久素材 |
| 48001 | 未授权使用该接口 | **订阅号未完成微信认证**。draft/add 等高级接口要求认证订阅号。去后台完成认证（需要企业资质）。 |
| 65304 | 草稿 media_id 已不存在 | 用户在后台删了草稿；重新发即可 |

---

## 订阅号 vs 服务号

本 Skill 针对**已认证订阅号**设计。差别：

|  | 订阅号（认证） | 服务号 |
|---|---|---|
| draft/add | ✅ | ✅ |
| material/add_material | ✅ | ✅ |
| media/uploadimg | ✅ | ✅ |
| 群发 | 每天 1 次 | 每月 4 次 |
| 阅读原文外链 | ❌（个人 App 到公众号无效）| ✅（可跳外链）|
| 支付接口 | ❌ | ✅ |

所以 Skill 只做到"创建草稿"为止，**群发由用户去后台手动点**。

---

## IP 白名单怎么查

公众号后台调用 API 前必须把服务器出口 IP 加到白名单：

1. 登录 `https://mp.weixin.qq.com`
2. 左侧菜单：**开发 → 基本配置 → IP 白名单**
3. 添加运行 Skill 的机器出口 IP

拿当前 IP：
```bash
curl -s ifconfig.me
```

如果你在 NAT 后面或换了网络，IP 会变，**记得更新白名单**。否则会报 40164。

---

## 调试技巧

- 每次 API 出错打印完整的 `{errcode, errmsg}` —— 微信错误信息很具体，不用瞎猜
- 如果连 token 都拿不到，**先用 curl 手工测试**，排除代码问题：
  ```bash
  curl "https://api.weixin.qq.com/cgi-bin/token?grant_type=client_credential&appid=YOUR_APPID&secret=YOUR_SECRET"
  ```
- 草稿创建成功后可以用 `/cgi-bin/draft/get?media_id=...` 把 HTML 拉回来看看微信解析后的样子，发现样式被改的立刻调整。
