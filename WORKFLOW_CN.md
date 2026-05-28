# Amazon GSPN 服务商官网识别工作流

目标：输入同格式 Amazon Seller Central Europe GSPN/SPN 服务商 CSV，输出每个服务商的独立官网链接，并保留证据与置信度。工作流按输入字段、运行参数、输出 schema、质量门禁和复核表固定下来，可在另一台电脑上复用。

## 当前输入源

```text
/Users/luojianyin/Documents/Codex/2026-05-22/https-sellercentral-europe-amazon-com-gspn/gspn_output_v3_live_20260522_192420/final_csv_with_descriptions/provider_details_final_with_field_descriptions.csv
```

已验证该文件：

```text
原始行数：2200
唯一 provider_id：1184
主要可用字段：provider_id、provider_name、service_api、detail_url、listing_logo_url、about_listing_text、service_types_json、provider_locations_json、provider_languages_json
```

这个文件没有官网字段，所以官网必须通过外部发现和证据验证得到。

## 为什么采用“候选发现 + 评分”而不是直接搜索第一条

Amazon SPN 是 Amazon 官方的第三方服务商网络，Amazon 说明这些 provider 会帮助卖家完成上架、运营、广告、图片、合规等工作；这证明输入数据是服务商目录，但不等于目录里自带官网字段。参考：[Amazon Service Provider Network](https://sell.amazon.com/tools/service-provider-network)。

搜索结果里经常混入：

- Amazon/Seller Central 详情页
- LinkedIn/Facebook/Instagram 等社媒页
- F6S、Fiverr、Upwork、Clutch 等平台页
- 公司注册页、目录页、声誉页
- 同名但无关的网站

所以最优方案不是“搜 provider_name 取第一个 URL”，而是：

1. 从 Amazon 输入字段生成多个搜索 query。
2. 全网/API 收集候选 URL，并用 `site:github.com` 把 GitHub 作为搜索空间之一。
3. 抓取候选官网首页和常见公司页面。
4. 用名称、域名、服务语义、地区、SPN 语义等证据评分。
5. 只对高分结果自动输出官网；中等分进入人工复核。

## 已实现项目结构

```text
/Users/luojianyin/Documents/官网搜索/
  README.md
  WORKFLOW_CN.md
  RUNBOOK_CN.md
  REPRODUCIBILITY_CN.md
  UNRESOLVED_NEXT_STEPS_CN.md
  TOOL_EVALUATION_CN.md
  Makefile
  .env.example
  requirements-optional.txt
  config/scoring.json
  finder/
    cli.py
    finalize.py
    input_normalizer.py
    query_builder.py
    search_sources.py
    scoring.py
    html_extract.py
    http.py
    text.py
  tools/evaluate_tools.py
  tools/build_review_sheet.py
  tools/apply_review.py
  tools/finalize_results.py
  tools/quality_gate.py
  tools/preflight_report.py
  tools/build_linked_workbook.py
  tools/plan_unresolved_second_pass.py
  tools/rebuild_from_evidence.py
  tools/verify_run_outputs.py
  tools/rescore_evidence.py
  tools/run_pipeline.py
  tools/summarize_tool_eval.py
  tests/test_workflow.py
  outputs/providers_normalized.csv
  outputs/sample_results.csv
  evidence/sample_evidence.jsonl
```

## 数据处理流程

接手执行优先看：[REPRODUCIBILITY_CN.md](/Users/luojianyin/Documents/官网搜索/REPRODUCIBILITY_CN.md) 和 [RUNBOOK_CN.md](/Users/luojianyin/Documents/官网搜索/RUNBOOK_CN.md)。

常用命令已封装在 `Makefile`：

```bash
make test
make prepare
make doctor
make sample
make eval-sample
make pipeline-demo
make pipeline SOURCE_CSV=/path/to/input.csv RUN_DIR=outputs/new_run
make build-xlsx RUN_DIR=outputs/new_run
make second-pass RUN_DIR=outputs/new_run
```

最简单交接命令：

```bash
./run_workflow.sh "/path/to/provider_details.csv" "outputs/new_run"
```

### 1. 输入标准化

原 CSV 是“服务商 x 服务类型/市场组合”粒度，需要先压成“服务商”粒度。

命令：

```bash
cd /Users/luojianyin/Documents/官网搜索

python3 -m finder.cli prepare \
  --input /Users/luojianyin/Documents/Codex/2026-05-22/https-sellercentral-europe-amazon-com-gspn/gspn_output_v3_live_20260522_192420/final_csv_with_descriptions/provider_details_final_with_field_descriptions.csv \
  --output outputs/providers_normalized.csv
```

当前验证结果：

```text
wrote 1184 normalized providers to outputs/providers_normalized.csv
```

### 2. 搜索 query 生成

每个 provider 会生成多类 query：

```text
"{provider_name}" official website
"{provider_name}" Amazon service provider
"{provider_name}" "{service_api}"
"{provider_name}" Seller Central
"{provider_name}" "{provider_location}" website
{provider_slug} website
site:github.com "{provider_name}"
site:github.com "{provider_name}" website
```

GitHub 信息的用途：

- 找 provider 自己的 GitHub organization/repository。
- 找 README、文档、代码注释中出现的品牌名或官网域名。
- 作为额外证据来源，不直接把 GitHub repo 当作官网输出。

这里不直接依赖 GitHub API。GitHub 通过 `site:github.com` 搜索 query 纳入全网搜索结果，避免把 GitHub API 变成主流程硬依赖。

### 3. 候选来源

已实现的来源：

- SerpAPI Google Search API：适合 Google SERP 结构化结果。参考：[SerpAPI Google Search API](https://serpapi.com/search-api)。
- Brave Search API：独立 web index，适合作为 Google 之外的第二搜索源。参考：[Brave Search API](https://brave.com/search/api/)。
- Tavily Search API：面向 AI/agent 的搜索结果和内容片段，适合作为补充。参考：[Tavily Search API](https://docs.tavily.com/api-reference/endpoint/search)。
- Serper Google Search API：低成本 Google SERP 候选源，已接入主流程，当前因没有 key 未真实调用。
- Firecrawl Search：搜索 + 抓取方向的补充源，已接入主流程，当前因没有 key 未真实调用。
- Exa Search API：语义搜索 + `contents.text/highlights`，当前用于 second-pass 难例补召回。
- DDGS：无需 key 的探索性搜索，适合测试和补漏，不建议作为唯一生产源。
- GitHub：通过 `site:github.com` query 收集公开信息，不直接调用 GitHub API。
- 直接域名猜测：如 `providername.com`，只作为低成本 fallback。

生产建议：

```text
优先级 1：Brave，作为主搜索源
优先级 2：Exa，作为 unresolved second-pass 语义补召回源
优先级 3：SerpAPI/Serper，用于 Google SERP 对照
优先级 4：Tavily 或 Firecrawl，作为补充搜索/抓取源
优先级 5：DDGS，用于 no-key 探索或补漏
优先级 6：domain guess，只作为补漏，不能单独视作充分证据
```

### 4. 候选过滤

不会把以下域名输出为官网：

```text
amazon.*
sellercentral.*
media-amazon.*
linkedin.com
facebook.com
instagram.com
youtube.com
tiktok.com
fiverr.com
upwork.com
clutch.co
goodfirms.co
scribd.com
slideshare.net
issuu.com
docplayer.net
opencorporates.com
company-information.service.gov.uk
scamadviser.com
trustpilot.com
```

这些站点可以作为佐证，但不是“独立官网”。

### 5. 官网验证与评分

每个候选站会抓取：

```text
/
/about
/about-us
/contact
/contact-us
/services
/amazon
/privacy
/terms
```

主要正向证据：

- 域名与 provider 名称高度匹配。
- 页面 title/body 出现完整 provider 名称。
- 安装可选依赖后，RapidFuzz 会增强名称/域名/标题的模糊匹配。
- 安装可选依赖后，Trafilatura 会优先用于静态官网正文抽取。
- JS-only 页面会被标记 `page_requires_javascript`；如启用可选 Playwright 动态渲染，会先尝试渲染后重新抽取正文，否则降为复核优先。
- 页面出现 Amazon、Seller Central、marketplace、PPC、FBA、catalog、compliance 等服务语义。
- 页面出现 provider location。
- 页面直接提到 Amazon SPN / Service Provider Network。
- 搜索结果来自 official website query 且排名靠前。

输出阈值：

```text
score >= 85      matched，自动输出官网
70 <= score <85  second-pass 需要强页面证据或多源支持才自动接受
50 <= score <70  second-pass 只有在“域名身份 + 页面/搜索/服务证据”同时成立时才自动接受；否则进入复核
45 <= score <50  needs_review，输出候选但建议人工复核
score < 45       low_confidence，不输出官网
无可用候选        not_found
```

## 运行方式

复制环境文件：

```bash
cp .env.example .env
```

填入至少一个搜索服务 key：

```bash
SERPAPI_API_KEY=...
BRAVE_API_KEY=...
TAVILY_API_KEY=...
SERPER_API_KEY=...
FIRECRAWL_API_KEY=...
EXA_API_KEY=...
DDGS_ENABLED=0
```

推荐生产组合是 `BRAVE_API_KEY + EXA_API_KEY`。Brave 跑普通精确 query，Exa 只跑 second-pass 语义 query，减少成本并提升 unresolved 命中率。

CLI 会自动读取当前目录的 `.env`。

如果只是先试工具，不配置付费 API，也可以安装可选工具：

```bash
python3 -m pip install --target .vendor_eval -r requirements-optional.txt
DDGS_ENABLED=1 FINDER_HTTP_TIMEOUT=8 PYTHONPATH=.vendor_eval:. python3 -m finder.cli run \
  --input outputs/providers_normalized.csv \
  --output outputs/sample_ddgs_results.csv \
  --evidence evidence/sample_ddgs_evidence.jsonl \
  --limit 20 \
  --per-query 3 \
  --max-candidates 20
```

JS-only 站点可以启用可选 Playwright 动态渲染兜底。默认在 `config/scoring.json` 里关闭，因为浏览器渲染明显慢于普通 HTTP 抓取；建议只在生产抽样确认 JS-only 噪声较多时开启：

```bash
python3 -m playwright install chromium
```

开启方式：

```json
"dynamic_rendering": {
  "enabled": true,
  "trigger_reasons": ["page_requires_javascript"],
  "timeout_ms": 8000
}
```

相关 evidence reason 包括 `dynamic_rendered_page`、`dynamic_render_unavailable`、`dynamic_render_failed`。

小样本测试：

```bash
python3 -m finder.cli run \
  --input outputs/providers_normalized.csv \
  --output outputs/sample_results.csv \
  --evidence evidence/sample_evidence.jsonl \
  --limit 20 \
  --per-query 5 \
  --max-candidates 30
```

全量运行：

```bash
PYTHONPATH=.vendor_eval:. python3 tools/preflight_report.py \
  --source /Users/luojianyin/Documents/Codex/2026-05-22/https-sellercentral-europe-amazon-com-gspn/gspn_output_v3_live_20260522_192420/final_csv_with_descriptions/provider_details_final_with_field_descriptions.csv \
  --run-dir outputs/production_run \
  --labels tests/fixtures/golden_expected_websites.csv \
  --soft-fail
```

先生成 `outputs/production_preflight.md/json`，确认没有阻塞项后再跑：

```bash
PYTHONPATH=.vendor_eval:. python3 tools/run_pipeline.py \
  --source /Users/luojianyin/Documents/Codex/2026-05-22/https-sellercentral-europe-amazon-com-gspn/gspn_output_v3_live_20260522_192420/final_csv_with_descriptions/provider_details_final_with_field_descriptions.csv \
  --run-dir outputs/production_run \
  --labels tests/fixtures/golden_expected_websites.csv \
  --batch-size 100 \
  --per-query 5 \
  --max-queries 6 \
  --max-candidates 30 \
  --min-official-url-rate 0.9 \
  --max-unresolved-rate 0.1
```

一键 pipeline 会生成：

```text
manifest.json
providers_normalized.csv
provider_official_websites.csv
provider_official_websites_evidence.jsonl
provider_review_queue.csv
provider_review_sheet_enhanced.csv
provider_final_official_websites.csv
provider_unresolved.csv
quality_gate_provider_final.md/json
```

没有生产搜索源时默认停止；no-key 演示需要显式 `--allow-exploratory`。

底层命令仍可单独运行：

```bash
python3 -m finder.cli run \
  --input outputs/providers_normalized.csv \
  --output outputs/provider_official_websites.csv \
  --evidence evidence/provider_official_websites_evidence.jsonl \
  --max-candidates 30
```

长任务建议分批运行并支持断点续跑：

```bash
python3 -m finder.cli run \
  --input outputs/providers_normalized.csv \
  --output outputs/provider_official_websites.csv \
  --evidence evidence/provider_official_websites_evidence.jsonl \
  --offset 0 \
  --limit 100 \
  --per-query 5 \
  --max-candidates 30

python3 -m finder.cli run \
  --input outputs/providers_normalized.csv \
  --output outputs/provider_official_websites.csv \
  --evidence evidence/provider_official_websites_evidence.jsonl \
  --offset 0 \
  --limit 200 \
  --per-query 5 \
  --resume \
  --max-candidates 30
```

`--resume` 会读取已存在的输出 CSV，跳过已有 `provider_id` 并追加新结果，适合 1184 个服务商分段执行。

`--max-candidates` 控制每个 provider 最多抓取和评分多少个候选 URL。真实 DDGS 试跑中，泛名称 provider 会显著拖慢批次；生产建议先用 20-30，质量不够再提高。

如果只改评分规则、阈值或排除域名，可以用已有 evidence 重新评分，不必重新搜索。已有候选分数会优先复用；旧 evidence 没有分数字段时才回退到正常评分路径：

```bash
python3 tools/rescore_evidence.py \
  --providers outputs/providers_normalized.csv \
  --evidence evidence/ddgs_10_capped_evidence.jsonl \
  --output outputs/ddgs_10_capped_rescored.csv
```

配置检查：

```bash
python3 -m finder.cli doctor --input outputs/providers_normalized.csv
```

结果审计和人工复核队列：

```bash
python3 -m finder.cli audit-results \
  --input outputs/provider_official_websites.csv \
  --review-output outputs/provider_review_queue.csv
```

增强复核表：

```bash
python3 tools/build_review_sheet.py \
  --results outputs/provider_official_websites.csv \
  --evidence evidence/provider_official_websites_evidence.jsonl \
  --output outputs/provider_review_sheet_enhanced.csv \
  --top-candidates 5
```

最终交付 CSV：

```bash
python3 tools/apply_review.py \
  --run-dir outputs/production_run \
  --review outputs/production_run/provider_review_sheet_enhanced.csv \
  --labels tests/fixtures/golden_expected_websites.csv \
  --min-domain-accuracy 0.9 \
  --min-auto-precision 0.95 \
  --min-official-url-rate 0.9 \
  --max-unresolved-rate 0.1
```

底层 finalizer：

```bash
python3 -m finder.cli finalize-results \
  --input outputs/provider_official_websites.csv \
  --review outputs/provider_review_queue.csv \
  --output outputs/provider_final_official_websites.csv \
  --unresolved-output outputs/provider_unresolved.csv
```

复核队列中人工填写：

```text
manual_decision=accept   接受当前候选
manual_decision=replace  使用 manual_url 替换
manual_decision=reject   确认无独立官网或候选错误
```

标注样本评测：

```bash
python3 tools/evaluate_labeled_results.py \
  --labels tests/fixtures/golden_expected_websites.csv \
  --results outputs/sample_results.csv \
  --output-md outputs/labeled_eval_sample_results.md \
  --output-json outputs/labeled_eval_sample_results.json
```

质量门禁：

```bash
python3 tools/quality_gate.py \
  --results outputs/provider_final_official_websites.csv \
  --labels tests/fixtures/golden_expected_websites.csv \
  --expected-rows 1184 \
  --min-domain-accuracy 0.9 \
  --min-auto-precision 0.95 \
  --min-official-url-rate 0.9 \
  --max-unresolved-rate 0.1 \
  --output-md outputs/quality_gate_provider_final.md \
  --output-json outputs/quality_gate_provider_final.json
```

当前 baseline 标注评测：

```text
5 个标注服务商
4 个官网域名命中
domain_accuracy = 0.8
auto_match_precision = 1.0
```

## 输出字段

```csv
provider_id,
provider_name,
official_url,
official_domain,
confidence,
status,
evidence_summary,
candidate_count,
scored_candidate_count,
service_apis,
provider_locations
```

最终交付 CSV 字段：

```csv
provider_id,
provider_name,
official_url,
official_domain,
status,
decision_source,
confidence,
source_status,
evidence_summary,
candidate_count,
scored_candidate_count,
service_apis,
provider_locations,
notes
```

最终状态：

```text
matched            自动高置信接受
manual_accepted    人工 accept / replace 后接受
rejected           人工确认不输出官网
unresolved         未复核或缺 URL，官网字段留空
```

证据文件为 JSONL，每行包含：

```json
{
  "provider_id": "...",
  "provider_name": "...",
  "result": {
    "official_url": "...",
    "confidence": 84,
    "status": "matched",
    "evidence_summary": "..."
  },
  "candidates": [
    {
      "url": "...",
      "domain": "...",
      "score": 84,
      "reasons": ["domain_exact_provider_slug", "page_contains_exact_provider_name"]
    }
  ]
}
```

## 已测试内容

自动测试：

```bash
python3 -m unittest discover -s tests
```

当前结果：

```text
Ran 54 tests
OK
```

覆盖点：

- 跳过 CSV 第一行中文字段说明。
- 合并同一 provider 的多服务行。
- 生成全网和 GitHub query。
- 排除 LinkedIn/Amazon 等非官网域名。
- 官方站高证据时输出 `matched`。
- 只有域名和名称匹配、缺少 Amazon 服务语义时降为 `needs_review`。
- 验证 SerpAPI、Brave、Tavily、DDGS 搜索源的响应解析和认证参数。
- 验证 Serper、Firecrawl、Exa 搜索源的响应解析和认证参数。
- 验证可选 Trafilatura/RapidFuzz 增强分支。
- 验证可选 Playwright 动态渲染分支，以及未安装浏览器依赖时的 fallback。
- 验证 `--offset`、`--append`、`--resume` 批量续跑能力。
- 验证标注样本评测脚本和 domain accuracy / auto-match precision 指标。
- 验证 doctor 配置检查、audit-results 结果审计。
- 验证 build_review_sheet 可把 evidence 候选展开为人工复核 CSV，并按当前排除域名规则重排 top candidate。
- 验证 apply_review 可在不重新搜索的情况下应用复核表、重写最终输出和 manifest。
- 验证 finalize-results 合并自动结果、人工复核决策和 unresolved 输出。
- 验证 rescore_evidence 可用当前排除域名重新评分已保存 evidence。
- 验证 quality_gate 可检查排除域名、重复 provider、URL 格式、行数、官网覆盖率、未解决率和标注精度。
- 验证 run_pipeline 可生成端到端 run directory 和 manifest，并在无生产搜索源时默认停止。

真实输入 smoke test：

```bash
python3 -m finder.cli run \
  --input outputs/providers_normalized.csv \
  --output outputs/sample_results.csv \
  --evidence evidence/sample_evidence.jsonl \
  --limit 5
```

当前样例：

```text
247EASYSUPPORT -> https://www.247easysupport.com/ -> matched, 84
247Tasker -> https://www.247tasker.com/ -> needs_review, 64
5starsecommerce -> https://5starsecommerce.com/ -> matched, 79
9THSIGHT PRIVATE LIMITED -> https://9thsight.com/ -> matched, 84
A2Z-ECOM -> low_confidence, 35
```

这说明评分逻辑没有把所有猜测域名都直接当作官网：缺少服务语义或站点不可访问时会进入复核或不输出。

工具调研和试跑记录见：[TOOL_EVALUATION_CN.md](/Users/luojianyin/Documents/官网搜索/TOOL_EVALUATION_CN.md)。

当前工具试跑摘要见：[tool_eval_summary.md](/Users/luojianyin/Documents/官网搜索/outputs/tool_eval_summary.md)。

当前标注样本评测见：[labeled_eval_sample_results.md](/Users/luojianyin/Documents/官网搜索/outputs/labeled_eval_sample_results.md)。

当前最终交付演示见：[final_demo_official_websites.csv](/Users/luojianyin/Documents/官网搜索/outputs/final_demo_official_websites.csv)。

当前 10 条 DDGS 受控样本复评见：[labeled_eval_ddgs_10_capped_rescored.md](/Users/luojianyin/Documents/官网搜索/outputs/labeled_eval_ddgs_10_capped_rescored.md)。

当前质量门禁报告见：[quality_gate_final_demo.md](/Users/luojianyin/Documents/官网搜索/outputs/quality_gate_final_demo.md) 和 [quality_gate_ddgs_10_capped_rescored.md](/Users/luojianyin/Documents/官网搜索/outputs/quality_gate_ddgs_10_capped_rescored.md)。

当前一键 pipeline 演示见：[pipeline_demo manifest](/Users/luojianyin/Documents/官网搜索/outputs/pipeline_demo/manifest.json)。

当前增强复核表示例见：[provider_review_sheet_enhanced.csv](/Users/luojianyin/Documents/官网搜索/outputs/pipeline_demo/provider_review_sheet_enhanced.csv)。

## 生产运行建议

1. 先用 `--limit 50` 跑样本。
2. 人工审查 `matched` 和 `needs_review` 各 20 条。
3. 如果 `matched` 误报高，调高 `auto_match_threshold` 到 80 或 85。
4. 如果漏报多，增加搜索源，优先加 Brave+Exa，再加 SerpAPI/Serper/Tavily/Firecrawl；GitHub 通过 `site:github.com` query 覆盖。
5. 全量跑完后，只把 `matched` 当作自动结果；`needs_review` 进入人工复核队列。

## 当前边界

- 没有生产搜索 API key 时，只能使用 DDGS 探索、域名猜测和页面验证；DDGS 不建议作为唯一生产源。
- 不建议直接爬 Google 搜索结果页或绕过 Amazon/Seller Central 限制。
- Amazon 目录数据证明该实体是 SPN/GSPN provider，但官网归属仍需要网页证据。
- 有些 provider 没有独立官网，或只经营 LinkedIn/Fiverr/Upwork 页面；这些应输出 `not_found` 或 `needs_review`，不应强行填 URL。
