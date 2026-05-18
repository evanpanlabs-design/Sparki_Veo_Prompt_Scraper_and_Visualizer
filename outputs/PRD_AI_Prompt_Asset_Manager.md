# PRD — AI Prompt 资产管理工具
## 飞书多维表格 AI Native 探索实践

---

## 一、项目背景与目标

### 背景

在 AI 产品设计工作中，产品经理日常需要：
- 持续追踪 AI 模型的最新能力（Veo 3.1、Gemini 等视频/图像生成模型）
- 收集优质 Prompt 作为团队资产，降低创意探索成本
- 将 AI 能力快速落地到业务场景（如多维表格的智能填充）

然而，市面上**没有**针对 AI 生成Prompt 的结构化管理工具。Prompt 通常散落在 Twitter、Discord、Notion 文档里，无法复用、无法检索、无法衡量质量。

### 目标

构建一个**AI Prompt 资产库**，实现：
1. **自动化采集** — 从 X(Twitter) 持续抓取 AI 生成Prompt，按质量过滤
2. **结构化存储** — 统一分类、标签化、附有质量指标（互动量、创作者影响力）
3. **可视化展示** — Web 界面支持按分类筛选、快速预览、查看完整 Prompt
4. **可扩展性** — Pipeline 设计支持多模型（Veo/Gemini/Sora）、多数据源

---

## 二、用户与场景

### 目标用户

| 用户角色 | 使用场景 | 核心需求 |
|---|---|---|
| **产品经理** | 设计 AI 功能时寻找灵感 | 按场景分类检索，复制可用 Prompt |
| **AI 研究员** | 评估某模型的内容生成质量 | 查看该模型下的优质 Prompt 样本 |
| **运营/设计师** | 快速生成特定风格的素材 | 按风格/情绪标签筛选 |

### 核心场景

**场景 A**：多维表格要上线"AI 批量生成封面图"功能
→ 产品经理在 Prompt 库中搜索"产品摄影""电商"分类，找到 12 条可复用的 Veo Prompt，直接粘贴到多维表格自动化流。

**场景 B**：多维表格计划接入视频生成能力
→ 产品经理查阅"video-generation"分类下的 Prompt，了解用户最常生成的视频类型（产品展示 > 场景模拟 > Logo 动画），制定优先级。

---

## 三、核心功能

### 3.1 数据采集层

**能力**：从 X(Twitter) 按关键词抓取原始推文，配合 LLM 判断是否为有效 Prompt

**关键技术决策**：
- **虚拟化 DOM 抓取**：X 采用虚拟化列表，DOM 仅保留 4-20 条推文。通过"每次滚动后立即提取全部推文"的方式，将一次滚动定义为一次抓取，而非依赖"新增推文"，避免因 DOM 重渲染导致数据丢失
- **LLM 二次验证**：推文文本先由 LLM 判断是否为有效 Prompt（避免采集到"求 Prompt"的回复贴），再提取结构化信息（分类、标题、正文）

**质量过滤**：
- 互动阈值：likes ≥ 50 或 retweets ≥ 20（过滤低质量推文）
- 创作者门槛：followers ≥ 1000（确保来源可信度）
- 去重：按 tweet_id 去重，同一推文不重复采集

### 3.2 数据结构

每条 Prompt 记录包含：

| 字段 | 说明 |
|---|---|
| `tweet_id` | 原始推文 ID（去重依据） |
| `author` | 创作者昵称、关注数 |
| `category` | LLM 分类（video-generation / image-generation 等） |
| `title` | 短标题（LLM 生成） |
| `prompt_text` | 完整 Prompt 正文 |
| `notes` | LLM 备注（如风格标签） |
| `engagement` | likes / retweets / replies / views |
| `url` | 原始推文链接 |

### 3.3 可视化界面

**卡片式 Prompt 展示**：
- 左侧分类筛选栏（动态显示各分类数量）
- 右侧 Prompt 卡片网格（标题、分类标签、2 行预览、互动数据）
- 点击"View Full Prompt"展开完整内容

**Hero 统计**：展示 Prompt 总数、分类数、创作者数（动态从数据库读取）

**发布流程**：本地生成 HTML → 推送至 GitHub Pages，支持增量更新

---

## 四、技术架构

```
[X.com 虚拟化列表]
       ↓  (scroll_and_scrape — 每次 PageDown 后全量提取)
[原始推文 JSON]
       ↓  (extract_prompts.py — LLM 分类 + 质量过滤)
[SQLite 数据库]
       ↓  (build_html.py — 读取 DB + 替换模板)
[veo3-prompt-library.html]
       ↓  (git push → GitHub Pages)
[静态网站]
```

**Pipeline 分工**：

| 模块 | 技术栈 | 职责 |
|---|---|---|
| 数据采集 | Playwright (无头浏览器) | 模拟滚动、抓取推文 DOM |
| Prompt 提取 | MiniMax LLM API (并发 15) | LLM 判断 + 结构化提取 |
| 数据存储 | SQLite | prompts / tweets / categories 表 |
| 前端展示 | 静态 HTML + Vanilla JS | 分类筛选、卡片展示 |
| 自动化发布 | Python 脚本 + GitHub Pages | 一命令发布到线上 |

---

## 五、关键产品决策记录

### 决策 1：滚动策略

**问题**：传统滚动采集方法在 X 虚拟化列表上失效——DOM 重用机制导致数据永远停在第 20 条。

**解决方案**：采用"一动一爬"策略（one-scroll-one-scrape），每次键盘按下 PageDown 后**立即提取 DOM 中全部推文**，通过全局 union set 累积数据，不再依赖"增量检测"。

**验证结果**：单次搜索可从 ~6 条/次提升至 ~150-200 条/次，采集效率提升 **25-30 倍**。

### 决策 2：LLM 提取质量修复

**问题**：LLM 提取存在两类失败模式：
1. JSON 结构被误读为标题（如 `"style":` 作为 prompt_text 返回）
2. Prompt 内容为空，返回占位符（如 `"..."` / `"[the full prompt text]"`）

**解决方案**：设计双层修复 Pipeline：
- 第一层：检测 prompt_text 是否为 JSON key name 或占位符
- 第二层：若检测到失败，重新调用 LLM，使用专为结构化 Prompt 设计的 Prompt Template 进行二次提取

**效果**：从完全失败到可用的修复率达到 50%+（针对结构化 JSON 类型 Prompt）。

### 决策 3：发布流程简化

**问题**：传统发布需要手动维护 HTML、传图片、推 Git，操作繁琐。

**解决方案**：`build_html.py --publish` 一命令完成：读取 DB → 替换 HTML 模板 → 复制图片 → git commit → push。全程无需手动介入，支持增量更新。

---

## 六、迭代路径（Roadmap）

| 阶段 | 内容 | 状态 |
|---|---|---|
| **基础采集** | X 爬虫 + LLM 提取 + SQLite 存储 | ✅ 已完成 |
| **数据质量管理** | 修复 JSON/占位符提取失败 | ✅ 已完成 |
| **界面优化** | 卡片 CSS 优化 + 分类筛选动态化 | ✅ 已完成 |
| **扩展数据源** | 支持 Discord / Midjourney 社区 | 🔲 待开发 |
| **多模态扩展** | 支持 Image Gen Prompt + 3D Asset Prompt | 🔲 待开发 |
| **飞书集成** | 导入飞书多维表格，作为 AI 素材库 | 🔲 待规划 |

---

## 七、数据成果

| 指标 | 数值 |
|---|---|
| 累计采集 Prompt | 29 条 |
| 分类数 | 4 个（video-generation 等） |
| 覆盖创作者 | 15+ 位 |
| Pipeline 运行时间 | ~3-5 分钟/次（含 LLM 并发 15） |
| GitHub Pages 浏览量 | 持续积累中 |

---

## 八、踩坑总结

### 踩坑 1：X 虚拟化列表导致采集停滞

**现象**：滚动后 DOM tweet 数量不增长，始终停留在 4-20 条。

**排查过程**：
1. 排除网络问题 → 直接检查 DOM 节点数，确认始终 4-20 条
2. 排除滚动方法 → 尝试 `window.scrollTo` / `mouse.wheel` / `PageDown`，结果相同
3. 确认根因：React 虚拟化渲染，DOM 是窗口池，超出窗口的 tweet 被回收

**解法**：改变采集逻辑——每次滚动后提取**全部 DOM tweet**，而非等待"新 tweet 出现"，通过全局 union set 累积。

### 踩坑 2：LLM 输出 JSON 解析失败

**现象**：`json.JSONDecodeError` 或解析出的字段为空。

**排查**：打印 raw response 发现 MiniMax 模型在 JSON 块后会追加一段 `<think>` 推理过程文本，导致 JSON 尾部被截断。

**解法**：定位最后一个 `}` 的位置（`raw.rfind("}")`），在 `<think>` 标记前截断，正确提取 JSON。

### 踩坑 3：Unicode 显示导致脚本中断

**现象**：`UnicodeEncodeError: 'gbk' codec can't encode character '\U0001d40c'`（部分 Unicode 字符无法在 Windows 终端打印）。

**解法**：将调试输出改为写 JSON 文件，而非 print 到终端，绕过 Windows GBK 编码限制。

---

## 九、对团队的价值

1. **AI Native 产品方法论验证**：探索了从数据采集→LLM处理→可视化展示的完整 Pipeline，验证了 AIGC 在内容资产管理场景的可行性
2. **可复用的技术方案**：scroll_and_scrape 模式、LLM repair pipeline 可迁移至其他社区（Discord/Reddit）的 Prompt 采集
3. **快速原型能力**：从 0 到完整 Web 页面的交付，证明了对 AI 工具链的掌握深度

---

*本 PRD 作为个人 AI 产品能力展示，不对外披露敏感信息。*