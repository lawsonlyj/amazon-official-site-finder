# Amazon GSPN 官网识别运行手册

本手册面向接手执行的人：从你给的 Amazon GSPN 服务商 CSV 出发，标准化输入，运行官网识别，审计结果，并用标注样本评估质量。

## 1. 输入和目标

输入源：

```text
/Users/luojianyin/Documents/Codex/2026-05-22/https-sellercentral-europe-amazon-com-gspn/gspn_output_v3_live_20260522_192420/final_csv_with_descriptions/provider_details_final_with_field_descriptions.csv
```

目标输出：

```text
outputs/provider_official_websites.csv
evidence/provider_official_websites_evidence.jsonl
outputs/provider_review_queue.csv
outputs/provider_final_official_websites.csv
outputs/provider_unresolved.csv
outputs/production_preflight.md
outputs/provider_official_websites_final_with_clickable_links.xlsx
```

最终交付 CSV 一行一个服务商，核心字段：

```text
provider_id, provider_name, provider_detail_url, listing_logo_url, official_url, official_domain, status, decision_source, confidence, source_status, evidence_summary, candidate_count, scored_candidate_count
```

## 2. 先检查环境

```bash
cd /Users/luojianyin/Documents/官网搜索
python3 -m finder.cli doctor --input outputs/providers_normalized.csv
```

如果没有生产搜索源，`production_ready` 会是 `false`。生产建议至少配置一个：

```text
BRAVE_API_KEY
SERPAPI_API_KEY
SERPER_API_KEY
FIRECRAWL_API_KEY
TAVILY_API_KEY
EXA_API_KEY
```

推荐生产组合是 `BRAVE_API_KEY + EXA_API_KEY`。Brave 负责常规精确搜索，Exa 只用于 second-pass 语义补召回；只有 Brave 也能运行，但 unresolved 命中率通常低一些。

`DDGS_ENABLED=1` 只建议用于探索和补漏，不建议作为 1184 条全量结果的唯一来源。

## 3. 一键常用命令

项目提供 `Makefile`，常用命令如下：

```bash
make test
make install-optional
make prepare
make doctor
make preflight
make sample
make eval-sample
make batch-demo
make finalize-demo
make rescore-ddgs-10
make quality-demo
make quality-ddgs-10
make review-demo
make pipeline-demo
make pipeline SOURCE_CSV=/path/to/input.csv RUN_DIR=outputs/new_run
make build-xlsx RUN_DIR=outputs/new_run
make second-pass RUN_DIR=outputs/new_run
```

最简单交接命令：

```bash
./run_workflow.sh "/path/to/provider_details.csv" "outputs/new_run"
```

第一次运行如果没有 `.env`，脚本会自动创建 `.env` 并提示填写 `BRAVE_API_KEY` 和可选 `EXA_API_KEY`。

可选 no-key 搜索试跑：

```bash
python3 -m pip install --target .vendor_eval -r requirements-optional.txt
make ddgs-sample
make eval-ddgs
```

如需处理 JS-only 官网候选，可额外安装 Playwright 浏览器二进制，并在 `config/scoring.json` 中把 `dynamic_rendering.enabled` 设为 `true`：

```bash
python3 -m playwright install chromium
```

`make preflight` 会生成 `outputs/production_preflight.md` 和 `outputs/production_preflight.json`，用于交接前确认输入规模、生产搜索源、可选依赖、质量门禁和推荐运行命令。当前没有生产 key 时它会标记 `NOT READY`，但 Makefile 使用 `--soft-fail` 保证仍能产出报告。

可选依赖默认安装到 `.vendor_eval`，生产命令建议带 `PYTHONPATH=.vendor_eval:.`，这样 Trafilatura、RapidFuzz、DDGS 和 Playwright 能被同一套脚本稳定发现。

拿到生产搜索 key 后，先跑一次 live check：

```bash
PYTHONPATH=.vendor_eval:. python3 tools/preflight_report.py \
  --source /Users/luojianyin/Documents/Codex/2026-05-22/https-sellercentral-europe-amazon-com-gspn/gspn_output_v3_live_20260522_192420/final_csv_with_descriptions/provider_details_final_with_field_descriptions.csv \
  --run-dir outputs/production_run \
  --labels tests/fixtures/golden_expected_websites.csv \
  --live-check
```

当前 Brave key 已完成 1184 条生产长跑；当前 second-pass 已升级为 Brave+Exa 召回 + seed-verification 救回。后续换成其他同格式输入时，使用 `./run_workflow.sh /path/to/input.csv outputs/new_run` 或 `make pipeline SOURCE_CSV=/path/to/input.csv RUN_DIR=outputs/new_run` 即可复用同一套流程。若运行中出现 `HTTP 402: Payment Required`，同一个 run directory 可以在补充 Brave 配额、添加 Exa/SerpAPI/Serper/Tavily/Firecrawl key 后用 `--resume` 继续，不会重跑已完成 provider。

## 4. 标准化输入

```bash
make prepare
```

等价于：

```bash
python3 -m finder.cli prepare \
  --input /Users/luojianyin/Documents/Codex/2026-05-22/https-sellercentral-europe-amazon-com-gspn/gspn_output_v3_live_20260522_192420/final_csv_with_descriptions/provider_details_final_with_field_descriptions.csv \
  --output outputs/providers_normalized.csv
```

当前已验证：

```text
2200 source rows -> 1184 unique providers
```

## 5. 小样本 smoke test

无搜索 API baseline：

```bash
make sample
make eval-sample
```

当前标注评测：

```text
5 labeled providers
4 domain matches
domain_accuracy = 0.8
auto_match_precision = 1.0
```

DDGS 探索样本：

```bash
make ddgs-sample
make eval-ddgs
```

当前标注评测：

```text
2 evaluated providers
2 domain matches
domain_accuracy = 1.0
auto_match_precision = 1.0
```

## 6. 生产全量运行

推荐使用一键 pipeline，所有产物会进入同一个 run directory，并写入 `manifest.json` 方便交接复盘。另一台电脑或新输入文件只需替换 `--source` 和 `--run-dir`：

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

便捷命令：

```bash
make pipeline SOURCE_CSV="/path/to/provider_details.csv" RUN_DIR="outputs/new_run"
```

这个便捷命令已接入 unresolved second-pass：首轮 final 之后会继续生成 `unresolved_second_pass_*`、`provider_final_official_websites_second_pass.csv` 和 `provider_official_websites_second_pass_with_clickable_links.xlsx`。如果只想对已有 run directory 补跑这一步：

```bash
make second-pass RUN_DIR="outputs/new_run"
```

second-pass 默认规则：

```text
Brave 跑 6 个精确 query。
Exa 默认跑 3 个语义 query，减少成本并提升难例召回。
未自动接受的行会继续做无 API 的 seed-verification 救回：只验证已有 top candidate、输入文本 URL 和保守品牌域名。
>=85 自动接受；70-84 需要强页面证据或多源支持；50-69 只有在“域名身份 + 页面/搜索/服务证据”同时成立时才自动接受；其余留在 review/unresolved。
GitHub 只通过 site:github.com 查询作为线索源，不直接作为官网。
```

没有生产搜索源时该命令会停止。只做 no-key 演示时才加 `--allow-exploratory`：

```bash
make pipeline-demo
```

如果生产运行中出现：

```text
HTTP Error 402: Payment Required
All production search API calls failed ... stopping to avoid domain-guess-only degradation
```

不要用纯域名猜测继续全量输出。先补充当前搜索源配额，或添加另一个生产搜索 key，然后用同一个 `--run-dir` 和 `--resume` 重新运行 pipeline。已完成行和 evidence JSONL 会被保留并跳过。

如果必须在没有新生产 key 的情况下先补漏，可以单独跑 DDGS 探索样本，不要混入生产 run directory：

```bash
BRAVE_API_KEY= DDGS_ENABLED=1 FINDER_HTTP_TIMEOUT=5 PYTHONPATH=.vendor_eval:. python3 -m finder.cli run \
  --input outputs/providers_normalized.csv \
  --output outputs/ddgs_remainder_after_brave_20.csv \
  --evidence evidence/ddgs_remainder_after_brave_20_evidence.jsonl \
  --offset 482 \
  --limit 20 \
  --per-query 3 \
  --max-queries 4 \
  --max-candidates 10
```

当前剩余区间 DDGS 20 条样本结果：16 `matched`、2 `needs_review`、2 `low_confidence`。它适合补漏和人工复核，不建议作为最终全量生产唯一来源。

推荐先跑 100 条：

```bash
python3 -m finder.cli run \
  --input outputs/providers_normalized.csv \
  --output outputs/provider_official_websites.csv \
  --evidence evidence/provider_official_websites_evidence.jsonl \
  --offset 0 \
  --limit 100 \
  --per-query 5 \
  --max-queries 6 \
  --max-candidates 30
```

继续扩大到前 200 条，跳过已经处理过的 provider：

```bash
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

跑完整 1184 条：

```bash
python3 -m finder.cli run \
  --input outputs/providers_normalized.csv \
  --output outputs/provider_official_websites.csv \
  --evidence evidence/provider_official_websites_evidence.jsonl \
  --offset 0 \
  --limit 1184 \
  --per-query 5 \
  --resume \
  --max-candidates 30
```

`--max-candidates` 是每个服务商进入正文抓取和评分的候选 URL 上限。真实 DDGS 试跑显示，泛名称服务商会产生大量慢站点；生产建议从 20-30 开始，抽检质量后再提高。

如果只是调整排除域名、阈值或评分逻辑，可以先用已有 evidence 重新评分，不必重新搜索。已有候选分数会优先复用；旧 evidence 没有分数字段时才回退到正常评分路径：

```bash
python3 tools/rescore_evidence.py \
  --providers outputs/providers_normalized.csv \
  --evidence evidence/ddgs_10_capped_evidence.jsonl \
  --output outputs/ddgs_10_capped_rescored.csv
```

## 7. 审计和人工复核队列

```bash
python3 -m finder.cli audit-results \
  --input outputs/provider_official_websites.csv \
  --review-output outputs/provider_review_queue.csv
```

建议同时生成增强复核表，把 evidence JSONL 中的候选 URL、域名、分数、搜索源、query 和命中理由展开成列。生成复核表时会重新应用当前排除域名规则，避免旧 evidence 里的社交/目录链接排成 top candidate：

```bash
python3 tools/build_review_sheet.py \
  --results outputs/provider_official_websites.csv \
  --evidence evidence/provider_official_websites_evidence.jsonl \
  --output outputs/provider_review_sheet_enhanced.csv \
  --top-candidates 5
```

处理规则：

- `matched`：可作为自动结果，但建议抽样。
- `needs_review`：人工复核，尤其是 JS-only、目录页强相关、只有名称匹配的情况。
- `low_confidence` / `not_found`：不自动输出官网，进入补搜或人工确认。

复核队列中的人工字段：

```text
manual_decision = accept  接受当前候选 URL
manual_decision = replace 使用 manual_url 替换
manual_decision = reject  确认不是官网或没有独立官网
```

如果只填写 `manual_url`、未填写 `manual_decision`，finalizer 会按 `replace` 处理，方便批量补录。

## 8. 生成最终交付 CSV

如果是从一键 pipeline 产物继续，推荐在填完 `provider_review_sheet_enhanced.csv` 后直接回写 run directory：

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

这个命令会重新生成：

```text
provider_final_official_websites.csv
provider_unresolved.csv
quality_gate_provider_final.md/json
manifest.json 中的 post_review 记录
```

底层命令也可以单独运行：

```bash
python3 -m finder.cli finalize-results \
  --input outputs/provider_official_websites.csv \
  --review outputs/provider_review_queue.csv \
  --output outputs/provider_final_official_websites.csv \
  --unresolved-output outputs/provider_unresolved.csv
```

规则：

- `matched` 且没有人工覆盖：自动进入最终 CSV。
- `accept`：接受复核队列里的当前候选 URL。
- `replace`：使用 `manual_url` 并重新计算 `official_domain`。
- `reject`：最终 CSV 保留 provider 行，但官网字段留空，状态为 `rejected`。
- 未复核的 `needs_review` / `low_confidence` / `not_found`：状态为 `unresolved`，官网字段留空。

生成可点击 XLSX：

```bash
python3 tools/build_linked_workbook.py \
  --sheet Final=outputs/provider_final_official_websites.csv \
  --sheet Auto_Results=outputs/provider_official_websites_enriched.csv \
  --sheet Review_Queue=outputs/provider_review_sheet_enhanced.csv \
  --output outputs/provider_official_websites_final_with_clickable_links.xlsx
```

## 9. 标注样本质量评测

```bash
python3 tools/evaluate_labeled_results.py \
  --labels tests/fixtures/golden_expected_websites.csv \
  --results outputs/provider_official_websites.csv \
  --output-md outputs/labeled_eval_provider_official_websites.md \
  --output-json outputs/labeled_eval_provider_official_websites.json
```

这个评测会输出：

```text
domain_accuracy
auto_match_precision
needs_review_rows
unresolved_rows
mismatches
```

小样本最终交付演示：

```bash
make finalize-demo
```

当前演示评测：

```text
5 labeled providers
5 domain matches
domain_accuracy = 1.0
auto_match_precision = 1.0
```

## 10. 质量门禁

交付前运行 quality gate，检查输出行数、重复 provider、排除域名是否漏出、URL 格式、状态合法性、官网覆盖率、未解决率，以及标注样本准确率：

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

小样本门禁：

```bash
make quality-demo
make quality-ddgs-10
```

当前两个样本门禁均为 `PASS`，并确认没有排除域名作为官网输出。

交付前还可以跑 run directory 完整校验：

```bash
python3 tools/verify_run_outputs.py \
  --run-dir outputs/production_run \
  --expected-rows 1184 \
  --xlsx outputs/production_run/provider_official_websites_final_with_clickable_links.xlsx
```

## 11. 最优方案结论

推荐组合：

```text
主搜索源：Brave Search API
second-pass 语义补召回：Exa Search API
低成本 Google SERP 对照：SerpAPI 或 Serper
正文增强补充源：Tavily 或 Firecrawl
GitHub 信息：site:github.com query，不依赖 GitHub API
正文抽取：Trafilatura
JS 兜底：Playwright 或 Firecrawl scrape
匹配：RapidFuzz + normalize_text
输出决策：规则评分 + evidence JSONL + review queue
```

Playwright 默认关闭，建议只对 JS-only 或高价值候选启用；相关 evidence reason 会记录为 `dynamic_rendered_page`、`dynamic_render_unavailable` 或 `dynamic_render_failed`。

当前生产样本状态：

```text
1184 rows
first pass: 731 matched, 453 unresolved
Brave+Exa + seed-verification second-pass: 288 accepted
final official_url rows: 1019
final unresolved rows: 165
quality gate PASS
excluded official URLs = 0
```

453 个 unresolved 的 second-pass 策略和当前输出见 [UNRESOLVED_NEXT_STEPS_CN.md](UNRESOLVED_NEXT_STEPS_CN.md)。
