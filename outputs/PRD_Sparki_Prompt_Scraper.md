# PRD — Sparki Prompt Scraper
## AI 生成 Prompt 数据采集与管理工具

---

## 一、背景与问题

### 市场现状

AI 模型（Veo 3.1、Gemini 2.5、Sora 等）的能力边界在持续扩展，产品经理、设计师、研究员在探索如何将 AI 生成能力落地到业务场景时，**缺乏结构化的 Prompt 参考库**。

优质 Prompt 散落在：
- X (Twitter) 上的 AI 创作者推文
- Discord AI 社区
- Midjourney社区
- Notion/Obsidian 个人笔记

无法快速检索、无法衡量质量、无法批量复用。

### 核心痛点

| 痛点 | 现状 | 期望 |
|---|---|---|
| **采集效率低** | 需要手动去 X 搜索、一个个找 | 自动滚动抓取 + LLM 过滤 |
| **数据无结构** | Prompt 存在 Excel/Notion 里 | 分类、标签、质量指标齐全 |
| **发布流程繁琐** | 每次更新要手动传 HTML + 图片 | 一命令发布到 GitHub Pages |
| **无法监控** | 不知道跑了多少条、成功失败 | 实时进度 + 日志 |

---

## 二、产品目标

构建一个**本地化 Prompt 采集工具**，帮助用户：
1. 从 X 按关键词自动采集 AI 生成 Prompt
2. 用 LLM 自动分类、提取标题和质量元数据
3. 一命令生成可发布的静态 HTML 页面
4. 通过 TUI 实时监控采集进度

---

## 三、用户与场景

### 目标用户

- **AI 产品经理**：构建团队 Prompt 资产库
- **AI 研究员**：采集某模型的优质 Prompt 样本
- **独立创作者**：积累可复用的 Prompt 素材

### 核心使用场景

```
用户启动 TUI
    ↓
配置 queries: ["Veo 3.1 prompt", "AI video generation"]
配置过滤条件: likes >= 50, followers >= 1000
配置 GCP bucket 信息
    ↓
启动采集任务
    ↓
实时看到：已滚动次数、采集推文数、LLM 提取成功/失败数
    ↓
完成后自动生成 HTML，发布到 GitHub Pages
```

---

## 四、核心功能

### 4.1 TUI 配置界面

用户通过键盘导航设置：

**Queries 配置**
- 添加/删除搜索关键词（如 "Veo 3.1 prompt"、"AI video generation"）
- 最多支持 20 个 query

**Engagement 过滤条件**
- `min_likes`: 默认 50
- `min_retweets`: 默认 20
- `min_followers`: 默认 1000

**GCS Bucket 配置**
- `bucket_name`: GCP bucket 名称
- `gcp_project_id`: GCP 项目 ID
- `service_account_json_path`: 服务账号 JSON 路径

### 4.2 实时任务面板

TUI 显示：

```
┌─────────────────────────────────────────────────────────┐
│  Sparki Prompt Scraper                    [Ctrl+C 退出]  │
├─────────────────────────────────────────────────────────┤
│                                                         │
│  状态: 🔄 采集中                      运行: 00:03:27   │
│                                                         │
│  Query: "Veo 3.1 prompt" (3/5)                         │
│                                                         │
│  滚动次数: 127/150    已采: 183条    成功: 12条         │
│  失败: 1条            跳过: 170条                        │
│                                                         │
│  最近日志:                                             │
│  [00:03:25] 🔍 开始搜索 query[3/5]: AI video prompt   │
│  [00:03:24] 📝 提取到 18 条推文 (累计 183)            │
│  [00:03:20] ⏳ LLM 分类中... (pending: 3)              │
│  [00:03:19] ✨ 新增 Prompt: "Cinematic Mountain Watch" │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

### 4.3 任务类型

| 模式 | 说明 |
|---|---|
| **Scrape** | 纯采集：跑 X 爬虫，采集推文入库，不调 LLM |
| **Extract** | LLM 提取：对数据库中已有推文做分类+提取 |
| **Pipeline** | 全量：从采集到提取到生成 HTML 全流程 |
| **HTML Only** | 仅生成：从 DB 读取已有 Prompt，生成 HTML |

### 4.4 数据存储

SQLite 本地数据库：

**表：queries**
- id, name, keyword, created_at, total_tweets, total_prompts

**表：tweets**
- id, tweet_id, text, author_name, author_screen, followers_count
- favorite_count, retweet_count, reply_count, view_count
- url, scraped_at, query_id

**表：prompts**
- id, tweet_id, url, category, title, prompt_text, notes
- author_name, author_screen, followers_count
- likes_count, retweet_count, reply_count, view_count
- image_gcs_url, scrape_id

**表：scrape_configs**
- id, query, min_likes, min_followers, min_retweets
- bucket_name, gcp_project_id, gcs_base_path, created_at

### 4.5 输出

- 本地 HTML 文件：`outputs/veo3-prompt-library.html`
- 可选：上传图片到 GCS（需配置 bucket）
- 可选：发布到 GitHub Pages

---

## 五、技术架构

```
┌──────────────────────────────────────────────────────┐
│                     TUI (Textual)                    │
│  ┌─────────────┐ ┌─────────────┐ ┌──────────────┐  │
│  │ Config Panel│ │Task Progress│ │  Log Viewer  │  │
│  └─────────────┘ └─────────────┘ └──────────────┘  │
└──────────────────────────────────────────────────────┘
                        │
                        ▼
              ┌─────────────────┐
              │  SQLite本地库   │
              │  tweets/prompts │
              └────────┬────────┘
                       │
                       ▼
         ┌────────────────────────────┐
         │      X Scraper (Playwright)│
         │   scroll_and_scrape()     │
         └────────────┬───────────────┘
                       │
                       ▼
         ┌────────────────────────────┐
         │   LLM Extract (MiniMax)    │
         │   process_tweet() + repair│
         └────────────┬───────────────┘
                       │
                       ▼
         ┌────────────────────────────┐
         │   HTML Generator           │
         │   build_html.py            │
         └────────────┬───────────────┘
                       │
                       ▼
              ┌──────────────┐
              │ GitHub Pages │
              │ (optional)   │
              └──────────────┘
```

**技术栈**：
- TUI 框架：`Textual`（Python，最适合 PowerShell 环境）
- 数据采集：Playwright（无头浏览器）
- LLM 调用：MiniMax API（支持 OpenAI-compatible 接口）
- 数据存储：SQLite
- HTML 生成：Python + Jinja2 模板

---

## 六、交互流程

```
启动工具
    │
    ▼
┌─────────────────────────────┐
│      主菜单（键盘导航）      │
│  [1] 开始新采集任务          │
│  [2] 继续上次任务            │
│  [3] 查看历史任务            │
│  [4] 配置                    │
│  [5] 退出                    │
└─────────────────────────────┘
```

**新建任务流程**：

```
1. 选择模式: [Scrape / Extract / Pipeline]
       │
       ▼
2. 配置 queries（多选，按 Enter 添加）
       │
       ▼
3. 配置过滤条件（默认值可改）
       │
       ▼
4. 配置 GCS（可选，跳过则不上传图片）
       │
       ▼
5. 确认并启动
       │
       ▼
┌──────────────────────────────┐
│      实时任务面板            │
│  - 滚动次数 / 采集数量       │
│  - LLM 成功率               │
│  - 实时日志流               │
│  - [P] 暂停 [R] 恢复 [Q] 停止│
└──────────────────────────────┘
       │
       ▼（完成）
6. 操作选项：
   - 生成 HTML
   - 发布到 GitHub Pages
   - 导出 JSON
```

---

## 七、验收标准

### 功能验收

| 功能 | 标准 |
|---|---|
| Query 配置 | 用户可添加/删除/编辑至少 5 个 query |
| 过滤条件 | min_likes/retweets/followers 可配置，保存到配置文件 |
| GCS 配置 | bucket name + project id + service account 可配置 |
| 实时进度 | 每秒更新滚动次数、采集数量、LLM 进度 |
| 日志流 | 最近 20 条日志在 TUI 中实时滚动显示 |
| 暂停/恢复 | P 键暂停任务，R 键恢复，不丢数据 |
| 停止确认 | Q 键停止时需二次确认 |

### 非功能验收

| 标准 | 要求 |
|---|---|
| 启动速度 | TUI 在 3 秒内显示主界面 |
| 响应性 | 键盘操作（方向键、Enter）在 100ms 内响应 |
| 兼容性 | 支持 PowerShell 5.1+（Windows 原生） |
| 可恢复性 | 任务中断后重新运行不重复采集已处理的 tweet_id |

---

## 八、配置持久化

配置文件：`~/.sparki/config.json`

```json
{
  "queries": [
    "Veo 3.1 prompt",
    "AI video generation",
    "Gemini image prompt"
  ],
  "engagement_filter": {
    "min_likes": 50,
    "min_retweets": 20,
    "min_followers": 1000
  },
  "gcs": {
    "bucket_name": "",
    "gcp_project_id": "",
    "service_account_json_path": ""
  },
  "llm": {
    "api_base": "https://api.minimaxi.com/v1",
    "model": "MiniMax-M2.7"
  },
  "github": {
    "token": ""
  }
}
```

---

## 九、文件结构

```
11_X_Scrape/
├── sparki_cli.py              # TUI 入口
├── scripts/
│   ├── x_multi_search.py     # X 爬虫（scroll_and_scrape）
│   ├── extract_prompts.py    # LLM 提取 + 修复
│   ├── build_html.py         # HTML 生成
│   └── db.py                 # SQLite 操作
├── outputs/
│   ├── generated_images/     # 本地图片缓存
│   └── veo3-prompt-library.html
└── data/
    └── sparki.db             # SQLite 数据库
```

---

## 十、迭代计划

| 版本 | 内容 |
|---|---|
| v0.1 | TUI 主框架 + 配置文件读写 + 主菜单 |
| v0.2 | 接入 x_multi_search.py（Scarpe 模式） |
| v0.3 | 接入 extract_prompts.py（Extract 模式） |
| v0.4 | 实时进度面板 + 日志流 |
| v0.5 | Pipeline 一键模式 + HTML 生成 |
| v1.0 | 暂停/恢复 + GCS 上传 + GitHub Pages 发布 |

---

*PRD v1.0 — 不含外部依赖敏感信息*