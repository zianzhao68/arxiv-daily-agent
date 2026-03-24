# arXiv Daily Papers Agent

## 项目简介

一个全自动的 arXiv 论文日报系统。每个工作日自动抓取 Embodied AI、World Models、Autonomous Driving 三个方向的新论文，通过 LLM 进行相关性过滤、深度分析和学术解读，生成结构化的 Markdown 日报并发送邮件摘要。

**核心流程**：arXiv 抓取 → 去重 → LLM 相关性过滤 → LLM 深度分析 → LLM 学术解读 → 生成报告 → 邮件推送

---

## 目录

1. [前置条件](#1-前置条件)
2. [仓库搭建](#2-仓库搭建)
3. [本地环境配置](#3-本地环境配置)
4. [Secrets 配置](#4-secrets-配置)
5. [自定义研究方向](#5-自定义研究方向)
6. [本地运行与调试](#6-本地运行与调试)
7. [GitHub Actions 自动运行](#7-github-actions-自动运行)
8. [产出物说明](#8-产出物说明)
9. [成本估算](#9-成本估算)
10. [常见问题](#10-常见问题)

---

## 1. 前置条件

在开始之前，你需要准备以下内容：

| 需要准备的内容 | 说明 |
|---|---|
| **GitHub 账号** | 用于托管代码和通过 GitHub Actions 运行流水线 |
| **Python >= 3.11** | 本地调试时需要；GitHub Actions 上会自动安装 |
| **OpenRouter API Key** | LLM 调用的统一网关，注册地址：https://openrouter.ai |
| **QQ 邮箱授权码**（可选） | 用于发送每日邮件摘要，不配置则跳过邮件功能 |

---

## 2. 仓库搭建

### 2.1 创建 GitHub 仓库

在 GitHub 上创建两个仓库：

| 仓库 | 用途 |
|---|---|
| `arxiv-daily-agent` | 主仓库，存放代码、配置、工作流 |
| `arxiv-daily-data` | 数据仓库，存放生成的报告和论文索引 |

> **为什么分两个仓库？**  数据仓库会随时间持续增长（每天追加报告），将其独立可以避免代码仓库体积膨胀，也方便单独浏览数据。

### 2.2 推送代码到主仓库

```bash
cd D:\PlayGround\Arxiv-rss-pull4

# 关联远程仓库（替换为你自己的地址）
git remote add origin https://github.com/<你的用户名>/arxiv-daily-agent.git

# 提交并推送
git add -A
git commit -m "init: arxiv daily papers agent"
git branch -M main
git push -u origin main
```

### 2.3 配置数据子模块

```bash
# 先确保 arxiv-daily-data 仓库已在 GitHub 上创建（空仓库即可）

# 删除本地 data 目录（将用 submodule 替代）
rm -rf data

# 添加子模块
git submodule add https://github.com/<你的用户名>/arxiv-daily-data.git data

# 在子模块中创建初始文件
cd data
mkdir reports weekly
echo '{"_meta":{"version":1,"last_updated":"","total_papers":0}}' > papers_index.json
git add -A
git commit -m "init: data structure"
git push

# 回到主仓库提交子模块引用
cd ..
git add .gitmodules data
git commit -m "add data submodule"
git push
```

---

## 3. 本地环境配置

### 3.1 安装 Python 依赖

```bash
# 建议使用虚拟环境
python -m venv .venv

# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
```

`requirements.txt` 包含以下依赖：

| 包名 | 用途 |
|---|---|
| `arxiv` | arXiv Search API 客户端 |
| `feedparser` | 解析 arXiv RSS 订阅 |
| `httpx` | 异步 HTTP 客户端，用于调用 OpenRouter API |
| `jinja2` | 模板引擎，用于渲染日报和邮件 |
| `pyyaml` | 加载 YAML 配置文件 |

### 3.2 设置环境变量

运行前需要设置以下环境变量：

```bash
# 必填 —— OpenRouter API 密钥
export OPENROUTER_API_KEY="sk-or-v1-xxxxxxxxxxxxxxxx"

# 可选 —— QQ 邮箱（不设置则跳过邮件发送）
export QQ_MAIL_ADDRESS="123456@qq.com"
export QQ_MAIL_AUTH_CODE="xxxxxxxxxxxxxxxx"
```

**Windows PowerShell** 用户：
```powershell
$env:OPENROUTER_API_KEY = "sk-or-v1-xxxxxxxxxxxxxxxx"
$env:QQ_MAIL_ADDRESS = "123456@qq.com"
$env:QQ_MAIL_AUTH_CODE = "xxxxxxxxxxxxxxxx"
```

---

## 4. Secrets 配置

在 GitHub 仓库的 **Settings → Secrets and variables → Actions** 中添加以下 Secrets：

| Secret 名称 | 说明 | 如何获取 |
|---|---|---|
| `OPENROUTER_API_KEY` | OpenRouter API 密钥 | 登录 https://openrouter.ai → Keys → Create Key |
| `PAT_TOKEN` | GitHub Personal Access Token | GitHub Settings → Developer settings → Personal access tokens → 创建一个具有 `repo` 权限的 Token |
| `QQ_MAIL_ADDRESS` | 你的 QQ 邮箱地址 | 例如 `123456@qq.com` |
| `QQ_MAIL_AUTH_CODE` | QQ 邮箱授权码（**不是**登录密码） | 见下方说明 |

### 4.1 获取 OpenRouter API Key

1. 访问 https://openrouter.ai 并注册/登录
2. 进入 **Keys** 页面，点击 **Create Key**
3. 复制生成的密钥（以 `sk-or-v1-` 开头）
4. 建议在 OpenRouter 后台设置月度预算上限（如 $20），防止意外超支

### 4.2 获取 QQ 邮箱授权码

1. 登录 https://mail.qq.com
2. 进入 **设置 → 账户**
3. 在 **POP3/IMAP/SMTP/Exchange/CardDAV/CalDAV 服务** 区域，开启 **POP3/SMTP 服务**
4. 按照提示发送短信验证后，系统会生成一个 **授权码**
5. 将这个授权码（不是 QQ 密码）填入 `QQ_MAIL_AUTH_CODE`

### 4.3 获取 GitHub PAT Token

1. 进入 GitHub **Settings → Developer settings → Personal access tokens → Tokens (classic)**
2. 点击 **Generate new token (classic)**
3. 勾选 `repo` 权限（完整的仓库读写权限）
4. 设定合理的过期时间（如 90 天），过期后需要重新生成
5. 复制 Token 并填入 `PAT_TOKEN`

> **为什么需要 PAT？** GitHub Actions 默认的 `GITHUB_TOKEN` 无法推送到子模块（另一个仓库）。PAT 需要同时对主仓库和数据仓库有写权限。

---

## 5. 自定义研究方向

所有的关键词和研究方向定义都在 `config/config.yaml` 中，**修改配置文件即可，无需改动代码**。

### 5.1 修改现有方向的关键词

以 Embodied AI 为例，找到 `research_directions.embodied_ai` 节：

```yaml
research_directions:
  embodied_ai:
    # 标题精确匹配 —— 论文标题中包含这些短语就会被抓取
    title_keywords:
      - "embodied AI"
      - "robot learning"
      - "humanoid robot"
      # 在这里添加新关键词...

    # 摘要精确匹配
    abstract_keywords:
      - "sim-to-real"
      - "tactile sensing"

    # 摘要组合匹配 —— 两个词必须同时出现在摘要中
    abstract_combos:
      - ["robot", "policy learning"]
      - ["manipulation", "foundation model"]

    # arXiv 类别过滤
    categories:
      - "cs.RO"
      - "cs.AI"
```

**三种关键词的区别**：

| 类型 | 匹配规则 | 精度 | 适用场景 |
|---|---|---|---|
| `title_keywords` | 论文标题包含该短语 | 高 | 非常明确的领域术语 |
| `abstract_keywords` | 论文摘要包含该短语 | 中 | 覆盖面更广的技术词汇 |
| `abstract_combos` | 摘要中同时出现列表内的所有词 | 高 | 单独出现含义模糊、组合后精确的词对 |

### 5.2 添加新的研究方向

在 `research_directions` 下添加一个新的键即可：

```yaml
research_directions:
  # ... 已有方向 ...

  medical_ai:
    title_keywords:
      - "medical image"
      - "clinical diagnosis"
    abstract_keywords:
      - "radiology"
      - "pathology"
    abstract_combos:
      - ["deep learning", "medical"]
    categories:
      - "cs.CV"
      - "cs.AI"
```

添加后，还需要同步修改 `prompts/relevance_filter.txt` 中的研究方向描述，让 LLM 知道如何判断新方向的相关性。

### 5.3 调整 LLM 模型

```yaml
models:
  relevance_filter:
    model_id: "google/gemini-2.5-flash"    # 过滤阶段用的模型（便宜、快）
    temperature: 0.1
    batch_size: 10                          # 每次发送 10 篇论文给 LLM 批量分类

  deep_analysis:
    model_id: "google/gemini-3.1-pro-preview"  # 深度分析用的模型（贵、准）
    temperature: 0.2
```

可以在 https://openrouter.ai/models 查看所有可用模型。如需更换模型，只需修改 `model_id`。

### 5.4 调整评分权重

```yaml
scoring:
  weights:
    novelty: 0.25          # 新颖性权重
    impact: 0.30           # 影响力权重
    reproducibility: 0.15  # 可复现性权重
    affiliation: 0.15      # 机构等级权重
    direction_match: 0.15  # 方向匹配度权重
  hot_threshold: 4.0       # 加权分 >= 4.0 的论文标记为 Hot
```

---

## 6. 本地运行与调试

### 6.1 完整运行

```bash
# 确保已设置 OPENROUTER_API_KEY 环境变量
python -m src.main
```

运行后会依次执行：
1. 从 arXiv 抓取论文（API + RSS 混合策略）
2. 与 `data/papers_index.json` 去重
3. 调用 Gemini Flash 进行相关性过滤
4. 调用 Gemini Pro 进行深度分析
5. 调用 Gemini Pro 生成学术解读（DeepResearch）
6. 生成 Markdown 日报到 `data/reports/YYYY-MM-DD.md`
7. 发送邮件（如果配置了 QQ 邮箱变量）

> 本地运行时会自动跳过 Git 推送步骤，不会影响远程仓库。

### 6.2 运行测试

```bash
pip install pytest
pytest tests/ -v
```

### 6.3 查看输出

运行成功后，输出文件位于：

```
data/
├── papers_index.json       ← 论文索引（持续累积）
└── reports/
    └── 2026-03-23.md       ← 当天的日报
```

---

## 7. GitHub Actions 自动运行

### 7.1 定时调度

工作流配置在 `.github/workflows/daily-run.yml` 中：

```yaml
on:
  schedule:
    - cron: '30 5 * * 1-5'   # UTC 05:30，即北京时间 13:30，周一到周五
  workflow_dispatch:          # 支持手动触发
```

**时间对照表**：

| 季节 | arXiv RSS 刷新 (UTC) | 本流水线运行 (UTC) | 缓冲时间 | 北京时间 |
|---|---|---|---|---|
| 冬令时 (EST) | 05:00 | 05:30 | 30 分钟 | 13:30 |
| 夏令时 (EDT) | 04:00 | 05:30 | 90 分钟 | 13:30 |

### 7.2 手动触发

在 GitHub 仓库页面：**Actions → Daily Papers Agent → Run workflow → Run workflow**

手动触发是幂等的——重复运行不会产生重复论文（因为去重基于 `papers_index.json`）。

### 7.3 查看运行日志

进入 **Actions** 页面，点击具体的运行记录即可查看每个阶段的 JSON 格式日志输出。

---

## 8. 产出物说明

### 8.1 日报 (`data/reports/YYYY-MM-DD.md`)

每份日报包含：

| 区域 | 内容 |
|---|---|
| **Overview** | 当天论文总数、各方向分布、最高分论文 |
| **Highlights** | 加权分 >= 4.0 的高亮论文，含完整分析 |
| **All Papers** | 所有论文按分数排序，含可展开的详细分析和 DeepResearch 解读 |

每篇论文附带的信息：

| 字段 | 说明 |
|---|---|
| 标签 | `Code` / `No Code` / `Dataset` / `Demo` / `Hot` / `Industry` / `Academic` / `Accepted` |
| 加权分 | 综合新颖性、影响力、可复现性、机构等级、方向匹配度 |
| 一行摘要 | 中文，30 字以内的核心贡献总结 |
| 详细分析 | 中文，3-5 段的结构化分析（问题→方法→结果→意义）|
| DeepResearch 解读 | 三模块学术解读（核心速写 + 架构解释 + 学术问答）|
| 链接 | arXiv 原文 / PDF / 中文翻译(hjfy.top) / 代码仓库 |

### 8.2 论文索引 (`data/papers_index.json`)

持续累积的 JSON 文件，记录所有处理过的论文及其分析结果。用于去重和后续的趋势分析。

### 8.3 邮件摘要

如果配置了 QQ 邮箱，每天会收到一封 HTML 格式的邮件，包含前 3 篇高亮论文的标题、摘要和链接。

---

## 9. 成本估算

所有 LLM 调用通过 OpenRouter 计费，以下是典型的每日成本：

| 阶段 | 模型 | 预估成本 |
|---|---|---|
| 相关性过滤 | Gemini 2.5 Flash | ~$0.003 |
| 深度分析 (10 篇) | Gemini 3.1 Pro Preview | ~$0.24 |
| **每日总计** | | **~$0.25** |
| **每月估算** (22 个工作日) | | **~$5.50** |

> 建议在 OpenRouter 后台设置月度预算上限，防止异常导致超支。

---

## 10. 常见问题

### Q: 运行报错 `Missing required environment variable: OPENROUTER_API_KEY`

确保已正确设置环境变量。本地运行用 `export`（Linux/macOS）或 `$env:`（PowerShell）；GitHub Actions 运行需要在仓库 Secrets 中配置。

### Q: 抓取不到论文 / 论文数量为 0

- arXiv 在周末和美国节假日不发布新论文，周一的运行会处理周五到周日的累积投稿
- 检查 `config/config.yaml` 中的关键词是否过于狭窄
- 首次运行时 `papers_index.json` 为空，不存在去重问题；如果是非首次运行，可能当天的论文都已在之前的运行中处理过

### Q: LLM 分析质量不理想

- 尝试在 `config/config.yaml` 中更换 `model_id`（如使用 `anthropic/claude-sonnet-4` 替代）
- 调整 `prompts/` 目录下对应的提示词文件，无需修改代码
- 降低 `temperature` 值使输出更确定性

### Q: 邮件发送失败

- 确认 QQ 邮箱已开启 POP3/SMTP 服务
- 确认使用的是**授权码**而非 QQ 登录密码
- 邮件发送失败不影响日报生成，报告仍会正常保存到 `data/reports/`

### Q: 如何重新处理某天的论文？

手动触发 GitHub Actions 即可。由于去重机制基于 `papers_index.json`，已处理的论文不会重复分析。如需完全重新处理，可以先从 `papers_index.json` 中删除对应的论文条目。

### Q: Git push 失败

- 检查 `PAT_TOKEN` 是否过期
- 确认 PAT 同时对主仓库和数据仓库有 `repo` 写权限
- 查看 Actions 日志中的具体报错信息

---

## 项目结构速览

```
arxiv-daily-agent/
├── .github/workflows/
│   └── daily-run.yml          # GitHub Actions 定时工作流
├── src/
│   ├── main.py                # 流水线主入口
│   ├── fetcher.py             # arXiv 抓取（API + RSS 混合）
│   ├── dedup.py               # 基于 ID 的去重
│   ├── relevance_filter.py    # LLM 相关性过滤（Gemini Flash）
│   ├── deep_analysis.py       # LLM 结构化分析（Gemini Pro）
│   ├── deep_research.py       # LLM 学术解读（三模块 DeepResearch）
│   ├── report_generator.py    # Markdown 日报生成
│   ├── email_sender.py        # QQ 邮箱 SMTP 发送
│   ├── git_ops.py             # 子模块 Git 操作
│   ├── llm_client.py          # OpenRouter API 封装
│   ├── models.py              # 数据模型定义
│   └── config.py              # 配置加载
├── prompts/
│   ├── relevance_filter.txt   # 相关性过滤提示词
│   ├── deep_analysis.txt      # 结构化分析提示词
│   └── deep_research.txt      # 学术解读提示词（支持模式一/模式二）
├── templates/
│   ├── daily_report.md.j2     # 日报 Markdown 模板
│   └── email_digest.html.j2   # 邮件 HTML 模板
├── config/
│   ├── config.yaml            # 关键词、模型、评分等全部配置
│   └── affiliations.json      # 机构等级白名单
├── data/                      # Git 子模块（数据仓库）
│   ├── papers_index.json      # 论文索引
│   └── reports/               # 日报存放目录
├── tests/                     # 单元测试
└── requirements.txt           # Python 依赖
```
