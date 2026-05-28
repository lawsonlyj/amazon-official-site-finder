# 工具调研、试跑结果与最优组合

目标不是直接把某个 API 写死进流程，而是比较“搜索候选、抓取网页、正文抽取、名称匹配、动态网页处理”这些环节分别适合用什么工具，然后确定最优组合。

机器可读版工具选择表见：[TOOL_SELECTION_MATRIX.csv](TOOL_SELECTION_MATRIX.csv)。

## 调研过的工具

| 环节 | 工具 | 类型 | 结论 |
|---|---|---:|---|
| 搜索候选 URL | Brave Search API | 付费/有免费额度 API | 推荐做生产主搜索源。独立搜索索引，API 结构稳定；当前 key 已通过 live check，并完成 10 条生产样本；全量长跑需要确认配额足够。 |
| 搜索候选 URL | SerpAPI Google Search | 付费 API | 推荐做高召回主搜索源或对照源。Google 结果质量通常好，但成本更高。 |
| 搜索候选 URL | Tavily Search API | 付费/开发者 API | 适合作为补充源，返回面向 agent 的摘要内容。 |
| 搜索 + 抓取 | Firecrawl Search | 付费 API | 已接入主流程并做响应解析测试；适合预算允许时使用，可搜索并返回 markdown/html，还支持 domain include/exclude 和 GitHub category。未真实调用，因为当前没有 key。 |
| 搜索候选 URL | Serper | 付费/有免费额度 API | 已接入主流程并做响应解析测试；Google SERP API，价格低，适合和 SerpAPI 做成本对比。未真实调用，因为当前没有 key。 |
| 搜索候选 URL | Bing Web Search API | Azure API | 适合企业合规场景，但排序与 Google 不同，需和 Brave/Google 类源交叉验证。未在本地实测，因为当前没有 Azure key。 |
| 搜索候选 URL | Apify Google Search Scraper | Actor/API | 适合批量 SERP 抓取和导出 JSON/CSV/Excel，但依赖 Apify Actor 运行成本。未在本地实测，因为当前没有 Apify token。 |
| 搜索 + 抓取 | ScrapingBee Google/SERP API | 付费 API | 适合 SERP 和网页抓取一体化，适合作为 Firecrawl 替代候选。未在本地实测，因为当前没有 key。 |
| 语义/神经搜索 | Exa Search API | 付费 API | 更适合语义搜索和找相似页面；本任务优先级低于 SERP API。未在本地实测，因为当前没有 key。 |
| 搜索候选 URL | ddgs | 开源/no-key | 已本地试跑。适合探索、样本验证、低成本补漏；不建议作为唯一生产源。 |
| 正文抽取 | trafilatura | 开源 | 已本地试跑。对普通官网抽取效果好；JS-only 网站会失败，需要动态渲染兜底。 |
| 动态网页 | Playwright | 开源浏览器自动化 | 适合处理 `JavaScript is required` 页面，但成本比普通 HTTP 抓取高。建议只对疑难候选兜底。 |
| 名称匹配 | RapidFuzz | 开源 | 已本地试跑。适合 provider name、域名、页面 title 的模糊匹配。 |
| GitHub 信息 | `site:github.com` 搜索 | 搜索策略 | 用普通搜索覆盖 GitHub，不把 GitHub API 作为主依赖。 |

参考资料：

- Amazon SPN 说明服务商网络是 vetted third-party service providers，并列出 Account management、Advertising optimization、Cataloging、Compliance 等服务类别：[Amazon SPN](https://sell.amazon.com/tools/service-provider-network)。
- SerpAPI 提供 Google Search API：[SerpAPI Search API](https://serpapi.com/search-api)。
- Brave 提供 Search API：[Brave Search API](https://brave.com/search/api/)。
- Tavily 提供 search endpoint：[Tavily Search API](https://docs.tavily.com/api-reference/endpoint/search)。
- Firecrawl search endpoint 支持 search + scrape、domain filters、`github` category：[Firecrawl Search](https://docs.firecrawl.dev/api-reference/endpoint/search)。
- Serper 提供 Google Search API：[Serper](https://serper.dev/)。
- Microsoft 提供 Bing Web Search API 文档：[Bing Web Search API](https://learn.microsoft.com/en-us/python/api/overview/azure/cognitiveservices/bing-web-search-api-readme?view=azure-python)。
- Apify 提供 Google Search Results Scraper：[Apify Google Search Scraper](https://apify.com/apify/google-search-scraper)。
- ScrapingBee 提供 Google/SERP 抓取 API：[ScrapingBee SERP API](https://www.scrapingbee.com/features/fast-search/)。
- Exa 提供 search endpoint：[Exa Search](https://docs.exa.ai/reference/search)。
- Trafilatura 是 Python 网页正文抽取工具：[Trafilatura docs](https://trafilatura.readthedocs.io/en/latest/)。
- ddgs 是聚合搜索库：[ddgs GitHub](https://github.com/deedy5/ddgs)。
- Playwright 可用于 Python 浏览器自动化：[Playwright Python](https://playwright.dev/python/docs/intro)。
- RapidFuzz 用于快速模糊匹配：[RapidFuzz docs](https://rapidfuzz.github.io/RapidFuzz/)。

## 本地实际试跑

安装到本地评测目录：

```bash
python3 -m pip install --target .vendor_eval -r requirements-optional.txt
```

### Brave live check 和生产样本

Brave Search API key 曾通过 live check，并完成样本；长跑期间如果出现 `HTTP 402: Payment Required`，说明当前 key 的配额或计费状态不足，需要补充配额或切换到另一个生产搜索源后用 `--resume` 继续：

```text
configured_sources: ["brave"]
production_ready: true
live_search_checks: brave ok
```

按 `--max-queries 6` 估算，1184 个 provider 的首轮全量约 7104 次 Brave 搜索请求。已完成 10 条 Brave 快速生产样本：

```text
run_dir: outputs/production_sample_10_brave_fast
result_rows: 10
matched: 6
needs_review: 3
low_confidence: 1
official_url_rate after auto-finalize: 0.6
labeled_domain_accuracy: 0.8
auto_match_precision: 1.0
```

已添加可复跑脚本：

```bash
PYTHONPATH=.vendor_eval:. python3 tools/evaluate_tools.py \
  --input outputs/providers_normalized.csv \
  --output outputs/tool_eval_ddgs_trafilatura.csv \
  --limit 5 \
  --max-results 5
```

本次真实样本输出：

```text
wrote 149 rows to outputs/tool_eval_ddgs_trafilatura.csv
```

量化摘要：

```bash
python3 tools/summarize_tool_eval.py \
  --input outputs/tool_eval_ddgs_trafilatura.csv \
  --output-md outputs/tool_eval_summary.md \
  --output-json outputs/tool_eval_summary.json
```

当前摘要：

```text
providers: 5
total_results: 149
usable_results: 110
excluded_results: 39
extractable_results: 80
strong_candidate_results: 23
```

新增 10 条受控 DDGS 工作流样本：

```bash
DDGS_ENABLED=1 FINDER_HTTP_TIMEOUT=5 PYTHONPATH=.vendor_eval:. python3 -m finder.cli run \
  --input outputs/providers_normalized.csv \
  --output outputs/ddgs_10_capped_results.csv \
  --evidence evidence/ddgs_10_capped_evidence.jsonl \
  --limit 10 \
  --per-query 2 \
  --max-candidates 10
```

结果审计：

```text
total_rows: 10
matched: 5
needs_review: 3
low_confidence: 2
domain_accuracy on 5 labeled rows: 1.0
auto_match_precision: 1.0
```

排除 document-sharing 域名后，用已有 evidence 重新评分：

```text
total_rows: 10
matched: 5
needs_review: 2
low_confidence: 3
domain_accuracy on 5 labeled rows: 1.0
auto_match_precision: 1.0
```

注意：`rescore_evidence` 只重算已有候选，不会发现新候选。单独重跑 `ALLIN INFO SYSTEMS PRIVATE LIMITED` 后，DDGS 可以发现 `allininfosystems.com`，但仍为 `needs_review`，因为页面证据不足以自动接受。

另有一套 5 条黄金样本评测，用于比较不同搜索源输出是否命中预期官网：

```bash
python3 tools/evaluate_labeled_results.py \
  --labels tests/fixtures/golden_expected_websites.csv \
  --results outputs/sample_results.csv \
  --output-md outputs/labeled_eval_sample_results.md \
  --output-json outputs/labeled_eval_sample_results.json
```

当前无生产搜索 API baseline：

```text
domain_accuracy: 0.8
auto_match_precision: 1.0
miss: A2Z-ECOM -> expected a2z-ecom.com
```

分 provider 观察：

```text
247EASYSUPPORT: 30 results, 4 strong candidates
247Tasker: 30 results, 0 strong candidates
5starsecommerce: 30 results, 8 strong candidates
9THSIGHT PRIVATE LIMITED: 29 results, 2 strong candidates
A2Z-ECOM: 30 results, 9 strong candidates
```

### ddgs 搜索观察

样本 query 结果显示：

- `"247EASYSUPPORT" official website` 第一条命中 `https://www.247easysupport.com/`。
- `"247Tasker" Amazon service provider` 结果混入新闻、博客和平台页，但也能发现 `247tasker.com`。
- `"5starsecommerce" website` 前几条混入 LinkedIn/TikTok/无关页面，说明 no-key 搜索噪声较大。
- `"9THSIGHT PRIVATE LIMITED" website` 前几条多为 Zauba、Tracxn、Planetexim 等公司注册/目录页，说明必须过滤目录域名。
- `"A2Z-ECOM" official website` 能发现 `https://a2z-ecom.com/Contact`，而直接 `a2zecom.com` 猜测失败。
- 10 条 DDGS 受控样本发现 `ALLIN INFO SYSTEMS PRIVATE LIMITED` 会命中文档分享站 `scribd.com`，因此已把 Scribd/SlideShare/Issuu/DocPlayer 加入排除域名。

结论：ddgs 可以做探索和补漏，但不够稳定，不应作为生产唯一来源。

### trafilatura 抽取观察

直接抽取测试：

```text
https://www.247easysupport.com/        2273 chars，有可用正文
https://www.247tasker.com/              95 chars，只返回 JavaScript required
https://5starsecommerce.com/           2060 chars，有可用正文
https://9thsight.com/                  4994 chars，有可用正文
https://a2z-ecom.com/Contact            223 chars，有可用正文
```

结论：

- 普通静态官网：trafilatura 明显优于手写 HTML parser。
- JS-only 网站：trafilatura 不够，需要 Playwright 或 Firecrawl 兜底。
- 不应该对所有搜索结果深抓；先过滤域名和候选，再抓 top candidates。
- 主评分已把 JS-only 页面作为复核信号：例如 DDGS 小样本中 `247Tasker` 从自动匹配降为 `needs_review`，证据摘要包含 `page_requires_javascript`。

### RapidFuzz 匹配观察

使用 `normalize_text` 后，RapidFuzz 对服务商名和域名/标题的匹配更合理：

```text
9THSIGHT PRIVATE LIMITED vs 9thsight      100
A2Z-ECOM vs a2z-ecom                     100
247EASYSUPPORT vs 247EasySupport         100
5starsecommerce vs 5Stars Ecommerce       96.8
```

结论：生产评分应使用“规范化 + 模糊匹配”，不要只用字符串完全相等。

## 最优方案

### 推荐生产组合

```text
主搜索源：Brave Search API 或 SerpAPI
补充搜索源：Tavily 或 Firecrawl Search
低成本 Google SERP 对照：Serper
GitHub 信息：通过 site:github.com query 纳入搜索，不直接依赖 GitHub API
正文抽取：trafilatura
动态网页兜底：Playwright 或 Firecrawl scrape
名称/域名匹配：RapidFuzz + normalize_text
最终决策：规则评分 + 人工复核队列
```

### 为什么这样选

1. **搜索阶段要高召回**  
   单一搜索源会漏掉官网。例如 A2Z-ECOM 的 `a2zecom.com` 猜测失败，但 `a2z-ecom.com` 可以通过搜索发现。

2. **GitHub 不应该作为独立 API 依赖**  
   这些 GSPN provider 大多是服务商/agency，不一定有 GitHub。GitHub 更适合作为搜索空间之一，例如 `site:github.com "provider_name"`，用来发现品牌域名或技术文档里的链接。

3. **抓取阶段要分层**  
   普通 HTTP + trafilatura 快；Playwright/Firecrawl 贵，只应对 JS-only 或高价值候选兜底。

4. **输出必须带置信度和人工复核状态**  
   公司注册页、目录页、新闻页经常看起来相关，但不是独立官网。`matched / needs_review / low_confidence / not_found` 是必要的。

## 当前项目对应实现

当前主项目已保留：

- 输入标准化：`finder/input_normalizer.py`
- query 生成：`finder/query_builder.py`
- 搜索适配：`finder/search_sources.py`
- 官网评分：`finder/scoring.py`
- 运行入口：`finder/cli.py`
- 结果审计：`finder/audit.py`
- 配置检查：`finder/doctor.py`
- 工具评测：`tools/evaluate_tools.py`
- 增强人工复核表：`tools/build_review_sheet.py`
- 复核结果回写：`tools/apply_review.py`
- 最终输出合并：`finder/finalize.py` / `tools/finalize_results.py`
- 质量门禁：`tools/quality_gate.py`
- 生产前交接审计：`tools/preflight_report.py`
- 一键运行和交接 manifest：`tools/run_pipeline.py`

已经按你的要求调整：

- 不再把 GitHub API 嵌入主工作流。
- GitHub 只通过 `site:github.com` 搜索 query 参与候选发现。
- Serper 和 Firecrawl 已接入主流程并有单元测试；真实调用需要对应 API key。
- Trafilatura 和 RapidFuzz 已作为可选增强接入评分；没有安装时自动 fallback 到标准库逻辑。
- Playwright 动态渲染已作为可选 JS-only 兜底接入评分，默认关闭；开启后 evidence 会记录 `dynamic_rendered_page`、`dynamic_render_unavailable` 或 `dynamic_render_failed`。
- 可选依赖统一建议安装到 `.vendor_eval`，运行生产 pipeline 时通过 `PYTHONPATH=.vendor_eval:.` 显式启用。
- 增加 `DDGS_ENABLED=1` 作为 no-key 评测入口。
- 增加 `--per-query` 控制搜索结果数，便于测试不同工具。
- 增加 `--max-candidates` 控制每个 provider 进入正文抓取和评分的候选 URL 数。
- 增加 `--offset`、`--append`、`--resume`，便于 1184 个 provider 分批续跑。
- 每处理完一个 provider 后 flush CSV 和 JSONL，降低长任务中断造成的结果丢失。
- 增加 `tools/rescore_evidence.py`，用于评分规则变更后快速复算已有 evidence。
- 增加 `tools/quality_gate.py`，用于交付前检查行数、重复 provider、排除域名、URL 格式、状态合法性、官网覆盖率、未解决率和标注样本精度。
- 增加 `tools/preflight_report.py`，用于生产前生成 Markdown/JSON 交接报告，检查输入规模、生产搜索源、可选依赖、质量门禁和推荐命令。
- 增加 `tools/run_pipeline.py`，用于把 prepare、run、audit、finalize、quality gate 串成一个可交接 run directory。
- 增加 `tools/build_review_sheet.py`，用于把 evidence 中的 top candidates、分数、来源和命中理由展开到人工复核 CSV。
- 增加 `tools/apply_review.py`，用于人工复核后无须重新搜索即可回写 final CSV、unresolved CSV、quality gate 和 manifest。

## 建议执行顺序

1. 用 `ddgs + trafilatura` 跑 20-50 条，快速观察噪声类型；建议 `--per-query 3 --max-candidates 20` 起步。
2. 选一个生产搜索源，优先 Brave 或 SerpAPI。
3. 用生产源跑 100 条，抽样审查 `matched` 和 `needs_review`。
4. 如果 JS-only 网站多，安装 Chromium 后把 `dynamic_rendering.enabled=true`，或加入 Firecrawl scrape 兜底。
5. 运行 `tools/preflight_report.py`，确认生产搜索源和门禁参数都 ready。
6. 全量跑 1184 个 provider，导出结果和 review queue。
7. 人工复核后运行 `tools/apply_review.py`，质量门禁通过后再交付最终 CSV。
