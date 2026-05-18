# Sparki — AI Prompt Scraper & Visualizer

从 X(Twitter) 自动采集 AI 生成 Prompt，通过 LLM 分类提取，生成可视化 HTML 页面。

## 快速开始

```bash
# 安装依赖
pip install playwright pandas pyyaml requests openai

# 初始化 cookies（从浏览器登录 x.com 后导出）
# 保存到 outputs/cookies.json

# 运行 Pipeline
python scripts/run_pipeline.py
```

## 项目结构

```
scripts/
├── x_multi_search.py      # X 爬虫（scroll_and_scrape）
├── extract_prompts.py     # LLM 提取 + 修复
├── build_html.py          # HTML 生成
├── db.py                  # SQLite 数据库
└── run_pipeline.py        # 完整 Pipeline 编排

outputs/
├── sparki_demo.html        # 前端 Demo（无需后端）
├── veo3-prompt-library.html  # 已生成的 Prompt 库页面
└── templates/             # HTML 模板
```

## 前端 Demo

打开 `outputs/sparki_demo.html` 即可预览 GUI，无需任何后端连接。

## 开发说明

- Python 3.11+
- Playwright（无头浏览器）
- MiniMax LLM API（OpenAI-compatible 接口）
- SQLite 本地数据库