# 项目目录说明

这个仓库现在是“产品交付版”：只保留能完整运行官网识别 workflow、Codex skill 调用、质量验证、可点击 XLSX 输出、人工复核学习闭环和测试验证的文件。

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
  run_review_cycle.sh

  config/
    scoring.json

  finder/
    *.py

  tools/
    apply_review.py
    build_linked_workbook.py
    build_manual_review_task.py
    run_agent_b_verification.py
    run_agent_c_recommendations.py
    apply_agent_optimizations.py
    build_review_sheet.py
    configure_env_from_key_files.py
    enrich_result_links.py
    evaluate_labeled_results.py
    finalize_results.py
    plan_unresolved_second_pass.py
    preflight_report.py
    quality_gate.py
    run_pipeline.py
    run_review_learning.py
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

### 人工复核后的闭环

完整流程跑完后，会自动生成一个简化复核表：

```text
outputs/my_run/manual_official_site_review_task.xlsx
```

工作人员只填三列：

```text
manual_decision：accept / replace / reject / unsure
manual_url：replace 时填真实官网；accept 且原链接正确时可不填
notes：可选，写判断依据
```

填完后，工作人员不需要再运行命令，只需要把填好的文件路径交给 Codex：

```text
Use amazon-official-site-finder skill.
Run directory: outputs/my_run
Filled review file: outputs/my_run/manual_official_site_review_task.xlsx
Please apply the review feedback, optimize the workflow where safe, verify everything, and report the final output files.
```

Codex 会在内部调用 `run_review_cycle.sh`，读取学习报告，执行安全的规则优化，并输出最终 reviewed 文件。

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
     -> tools/build_manual_review_task.py
     -> 可选 tools/run_agent_b_verification.py
     -> tools/verify_run_outputs.py
```

人工复核后由 Codex 接手：

```text
Codex receives filled manual review workbook
  -> run_review_cycle.sh --update-config
  -> tools/run_review_learning.py
     -> 合并 second-pass 决策和人工填写结果
     -> 重新生成 reviewed final/unresolved
     -> 生成 manual_review_labels.csv
     -> 重新跑 quality gate
     -> 生成 manual_review_learning_report.md
     -> 对重复出现且安全的排除域名更新 config/scoring.json
     -> tools/build_linked_workbook.py
  -> tools/run_agent_c_recommendations.py
     -> 汇总 AgentB 和人工复核学习报告里的重复模式
  -> tools/apply_agent_optimizations.py --apply
     -> 只应用安全、可解释的 excluded_domains 更新
  -> tools/verify_run_outputs.py
  -> Codex 读取 learning report 并汇报最终输出
```

### 每个关键文件做什么

| 文件或目录 | 作用 |
|---|---|
| `run_codex_assisted.sh` | 给 Codex 用的一键入口。先从 key 文件生成 `.env`，再启动完整 workflow。 |
| `tools/configure_env_from_key_files.py` | 从 Brave/Exa key 文件读取密钥，写入 `.env`，但不会在输出里打印密钥。 |
| `run_workflow.sh` | 普通用户的一键入口。检查 `.env`、安装可选依赖、跑 preflight、pipeline 和最终验证。 |
| `run_review_cycle.sh` | Codex 在人工复核填完后内部调用。把反馈应用回结果，生成 reviewed 输出和学习报告，可启用安全规则优化。 |
| `tools/preflight_report.py` | 开跑前检查输入文件、API key、依赖和搜索 API 是否可用。 |
| `tools/run_pipeline.py` | 主调度器。负责标准化输入、搜索候选、评分、生成初版结果、质量门禁和 second-pass。 |
| `finder/` | 核心逻辑包。包括输入清洗、搜索 query 构建、API 搜索、网页抓取、官网评分。 |
| `tools/run_unresolved_second_pass.py` | 对第一轮没解决的商家做二轮补漏，用 Brave/Exa 找更可能的官网。 |
| `tools/build_manual_review_task.py` | 生成简化人工复核 CSV/XLSX，只保留工作人员需要判断和填写的列。 |
| `tools/run_agent_b_verification.py` | AgentB 候选优先复核。先验证当前候选官网，再做少量独立搜索，输出 accept/replace/reject/unsure 和结构化证据。 |
| `tools/run_review_learning.py` | 读取填好的复核表，合并人工反馈，输出 reviewed 结果、人工标签和优化建议。 |
| `tools/run_agent_c_recommendations.py` | AgentC 读取 AgentB 结果和人工复核学习报告，输出可执行或需人工评估的优化建议。 |
| `tools/apply_agent_optimizations.py` | AgentA 安全应用器，只自动写入可解释、可回滚的 excluded_domains 配置。 |
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
outputs/my_run/manual_official_site_review_task.xlsx
outputs/my_run/manual_official_site_review_task.csv
outputs/my_run/agent_b_verification_results.csv
outputs/my_run/agent_b_verification_results.xlsx
```

人工复核填完并交给 Codex 后，最终主要看：

```text
outputs/my_run/provider_final_official_websites_reviewed.csv
outputs/my_run/provider_official_websites_reviewed_with_clickable_links.xlsx
outputs/my_run/provider_unresolved_reviewed.csv
outputs/my_run/manual_review_learning_report.md
outputs/my_run/manual_review_labels.csv
outputs/my_run/agent_c_optimization_recommendations.md
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
