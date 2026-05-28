# 项目目录说明

这个仓库现在是“产品交付版”：只保留能完整运行官网识别 workflow、Codex skill 调用、质量验证、可点击 XLSX 输出和测试验证的文件。

## 最终目录

```text
amazon-official-site-finder/
  README.md
  Makefile
  .env.example
  .gitignore
  requirements-optional.txt
  run_workflow.sh
  run_codex_assisted.sh

  config/
    scoring.json

  finder/
    *.py

  tools/
    apply_review.py
    build_linked_workbook.py
    build_review_sheet.py
    configure_env_from_key_files.py
    enrich_result_links.py
    evaluate_labeled_results.py
    finalize_results.py
    plan_unresolved_second_pass.py
    preflight_report.py
    quality_gate.py
    run_pipeline.py
    run_unresolved_second_pass.py
    verify_run_outputs.py

  tests/
    test_workflow.py
    fixtures/

  codex-skills/
    amazon-official-site-finder/
      SKILL.md

  docs/
    PROJECT_STRUCTURE_CN.md
    guides/
      amazon_official_site_finder_user_guide_cn_20260528.pdf
      amazon_official_site_finder_codex_skill_guide_cn_20260528.pdf
```

## 一位工作人员怎么使用

### 方式 A：普通脚本运行

工作人员只需要准备：

1. 同格式 Amazon provider CSV。
2. `.env` 文件，里面填好 `BRAVE_API_KEY`，最好也填 `EXA_API_KEY`。

运行：

```bash
cp .env.example .env
# 填好 .env 后：
./run_workflow.sh "/path/to/provider_details.csv" "outputs/my_run"
```

### 方式 B：Codex 自动操作

工作人员只需要准备：

1. Brave key 文件。
2. Exa key 文件。
3. 同格式 Amazon provider CSV。
4. 已安装 Codex skill。

然后在 Codex 里说：

```text
Use amazon-official-site-finder skill.
Brave key file: /path/to/brave_key.txt
Exa key file: /path/to/exa_key.txt
Input CSV: /path/to/provider_details.csv
Output directory: outputs/my_run
Please configure, run, verify, and report the final output files. Do not print API keys.
```

Codex 会调用：

```bash
./run_codex_assisted.sh \
  --brave-key-file "/path/to/brave_key.txt" \
  --exa-key-file "/path/to/exa_key.txt" \
  --source "/path/to/provider_details.csv" \
  --run-dir "outputs/my_run"
```

## 文件运行顺序

### Codex-assisted 顺序

```text
run_codex_assisted.sh
  -> tools/configure_env_from_key_files.py
  -> run_workflow.sh
     -> tools/preflight_report.py
     -> tools/run_pipeline.py
        -> finder/
        -> tools/build_review_sheet.py
        -> tools/enrich_result_links.py
        -> tools/quality_gate.py
        -> tools/run_unresolved_second_pass.py
           -> tools/plan_unresolved_second_pass.py
           -> tools/build_linked_workbook.py
     -> tools/verify_run_outputs.py
```

### 每个关键文件做什么

| 文件或目录 | 作用 |
|---|---|
| `run_codex_assisted.sh` | 给 Codex 用的一键入口。先从 key 文件生成 `.env`，再启动完整 workflow。 |
| `tools/configure_env_from_key_files.py` | 从 Brave/Exa key 文件读取密钥，写入 `.env`，但不会在输出里打印密钥。 |
| `run_workflow.sh` | 普通用户的一键入口。检查 `.env`、安装可选依赖、跑 preflight、pipeline 和最终验证。 |
| `tools/preflight_report.py` | 开跑前检查输入文件、API key、依赖和搜索 API 是否可用。 |
| `tools/run_pipeline.py` | 主调度器。负责标准化输入、搜索候选、评分、生成初版结果、质量门禁和 second-pass。 |
| `finder/` | 核心逻辑包。包括输入清洗、搜索 query 构建、API 搜索、网页抓取、官网评分。 |
| `tools/run_unresolved_second_pass.py` | 对第一轮没解决的商家做二轮补漏，用 Brave/Exa 找更可能的官网。 |
| `tools/build_linked_workbook.py` | 生成链接可点击的 XLSX。 |
| `tools/verify_run_outputs.py` | 检查最终 CSV、unresolved CSV、质量 JSON、XLSX 链接公式是否正常。 |
| `tools/apply_review.py` | 人工复核后，把人工 decision 应用回已有 run。 |
| `tests/` | 自动化测试，确保精简或改代码后 workflow 没坏。 |
| `docs/guides/` | 给工作人员看的 PDF 教程。 |

## 最终输出文件

运行成功后，主要看这几个文件：

```text
outputs/my_run/provider_final_official_websites_second_pass.csv
outputs/my_run/provider_official_websites_second_pass_with_clickable_links.xlsx
outputs/my_run/provider_unresolved_second_pass.csv
outputs/my_run/quality_gate_provider_second_pass_final.json
```

## 本地生成但不提交的目录

```text
.env
.vendor_eval/
.cache/
outputs/
evidence/
.spreadsheet_build/
```
