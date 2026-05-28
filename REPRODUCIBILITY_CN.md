# 可复现工作流说明

这里的“可复现”指：另一台电脑拿到同样字段结构的 Amazon GSPN/SPN 商家 CSV 后，可以用同一套命令生成同样结构的输出，而不是只复建本次 1184 行的历史结果。

## 1. 输入契约

输入 CSV 可以包含中文字段说明行，工作流会自动跳过。至少需要这些业务字段：

```text
provider_id
provider_name
service_api
detail_url
listing_logo_url
about_listing_text
service_types_json
provider_locations_json
provider_languages_json
```

同一个 `provider_id` 可跨服务类别重复出现；标准化步骤会合并成一行一个 provider。

## 2. 环境契约

基础流程只依赖 Python 标准库：

```bash
python3 --version
```

推荐安装可选依赖，用于更好的正文抽取、模糊匹配和 DDGS 探索：

```bash
python3 -m pip install --target .vendor_eval -r requirements-optional.txt
```

复制环境变量模板：

```bash
cp .env.example .env
```

生产运行至少配置一个生产搜索源：

```text
BRAVE_API_KEY 或 SERPAPI_API_KEY
```

推荐配置：

```text
BRAVE_API_KEY=...
EXA_API_KEY=...
```

Brave 负责精确搜索召回，Exa 只用于 unresolved second-pass 的语义补召回。只有 Brave 也能跑完整流程；有 Exa 时 second-pass 命中率更高。

`DDGS_ENABLED=1` 只作为探索和补漏，不建议作为唯一生产源。

## 3. 最简单通用运行命令

另一台电脑拿到同格式新输入 CSV 后，最简单方式是：

```bash
cd /path/to/official-site-finder
cp .env.example .env
# 编辑 .env，填 BRAVE_API_KEY 和可选 EXA_API_KEY
./run_workflow.sh "/path/to/provider_details.csv" "outputs/my_new_run"
```

脚本会自动安装可选依赖、执行 live preflight、跑完整 pipeline、跑 Brave+Exa + seed-verification second-pass、生成可点击 XLSX，并做输出校验。

## 4. Makefile 通用运行命令

换成任意同格式输入时，只需要覆盖 `SOURCE_CSV` 和 `RUN_DIR`：

```bash
make pipeline \
  SOURCE_CSV="/path/to/provider_details_final_with_field_descriptions.csv" \
  RUN_DIR="outputs/my_new_run"
```

`make pipeline` 会执行首轮搜索、审计、复核表生成、最终 CSV、质量门禁，并继续执行 unresolved second-pass。second-pass 会生成新的候选、自动接受少量强证据结果，并输出单独的 second-pass final，避免覆盖首轮 final。

等价的底层命令：

```bash
PYTHONPATH=.vendor_eval:. python3 tools/run_pipeline.py \
  --source "/path/to/provider_details_final_with_field_descriptions.csv" \
  --run-dir "outputs/my_new_run" \
  --labels tests/fixtures/golden_expected_websites.csv \
  --batch-size 50 \
  --per-query 3 \
  --max-queries 6 \
  --max-candidates 10 \
  --resume \
  --run-second-pass \
  --second-pass-max-search-queries 6 \
  --second-pass-accept-threshold 70 \
  --second-pass-write-xlsx \
  --min-domain-accuracy 0.8 \
  --min-auto-precision 0.95 \
  --min-official-url-rate 0.5 \
  --max-unresolved-rate 0.6
```

生成可点击 XLSX：

```bash
make build-xlsx RUN_DIR="outputs/my_new_run"
```

如果只想在已有 run directory 上补跑 unresolved second-pass：

```bash
make second-pass RUN_DIR="outputs/my_new_run"
```

## 5. 输出契约

每个 run directory 至少包含：

```text
providers_normalized.csv
provider_official_websites.csv
provider_official_websites_enriched.csv
provider_official_websites_evidence.jsonl
provider_review_queue.csv
provider_review_sheet_enhanced.csv
provider_final_official_websites.csv
provider_unresolved.csv
quality_gate_provider_final.md
quality_gate_provider_final.json
manifest.json
unresolved_second_pass_plan.csv
unresolved_second_pass_results.csv
unresolved_second_pass_evidence.jsonl
unresolved_second_pass_review_decisions.csv
provider_final_official_websites_second_pass.csv
provider_unresolved_second_pass.csv
quality_gate_provider_second_pass_final.md
quality_gate_provider_second_pass_final.json
```

最终交付 CSV 固定字段：

```text
provider_id
provider_name
provider_detail_url
listing_logo_url
official_url
official_domain
status
decision_source
confidence
source_status
evidence_summary
candidate_count
scored_candidate_count
service_apis
provider_locations
notes
```

XLSX 输出：

```text
provider_official_websites_final_with_clickable_links.xlsx
provider_official_websites_second_pass_with_clickable_links.xlsx
```

其中 URL 字段使用 `HYPERLINK()` 公式，可在 Excel、Numbers、Google Sheets 中点击。

## 6. 可复现校验

新 run 完成后运行：

```bash
python3 tools/verify_run_outputs.py \
  --run-dir "outputs/my_new_run" \
  --expected-rows <标准化后的 provider 数> \
  --xlsx "outputs/my_new_run/provider_official_websites_final_with_clickable_links.xlsx"
```

校验 second-pass final：

```bash
python3 tools/verify_run_outputs.py \
  --run-dir "outputs/my_new_run" \
  --final provider_final_official_websites_second_pass.csv \
  --unresolved provider_unresolved_second_pass.csv \
  --quality quality_gate_provider_second_pass_final.json \
  --expected-rows <标准化后的 provider 数> \
  --xlsx "outputs/my_new_run/provider_official_websites_second_pass_with_clickable_links.xlsx"
```

本次历史 run 的校验命令：

```bash
make verify-current-second-pass
```

当前通过条件：

```text
final_rows = 1184
official_url_rows = 1019
unresolved_rows = 165
provider_detail_url_rows = 1184
quality_passed = true
xlsx_hyperlink_formulas = 5026
xlsx_formula_errors = 0
```

## 7. second-pass 规则

当前 second-pass 的默认策略：

```text
Brave: 精确 query 主召回
Exa: 默认 3 个语义补召回 query
GitHub: 通过 site:github.com query 作为线索源，不作为最终官网证据
seed verification: 对未接受行无 API 验证已有 top candidate、输入 URL 和保守品牌域名
阈值: >=85 自动接受；70-84 需要强页面证据或多源支持；50-69 必须同时有域名身份和页面/搜索/服务证据；其他进入 review/unresolved
过滤: Amazon/social/directory/video/domain-sale/parked/login/password/suspended URL 不自动接受
```

## 8. 确定性重建和联网重跑的区别

从已有 `provider_official_websites_evidence.jsonl` 重建结果是确定性的，适合审计、交接和修正规则后复算：

```bash
PYTHONPATH=.vendor_eval:. python3 tools/rebuild_from_evidence.py \
  --run-dir "outputs/my_new_run" \
  --labels tests/fixtures/golden_expected_websites.csv \
  --expected-rows <标准化后的 provider 数> \
  --build-xlsx
```

重新联网搜索不是字节级确定性的，因为搜索索引、网页内容、跳转和 API 排名会随时间变化。工作流保证的是：输入字段、参数、候选收集逻辑、评分规则、排除域名、输出 schema、质量门禁和复核流程一致。

## 9. 交接清单

交给别人时至少给这些文件：

```text
README.md
RUNBOOK_CN.md
REPRODUCIBILITY_CN.md
WORKFLOW_CN.md
Makefile
.env.example
run_workflow.sh
config/
finder/
tools/
tests/
requirements-optional.txt
```

不要交付真实 `.env`，只交付 `.env.example`。
