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
    apply_pattern_release_to_run.py
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
outputs/my_run/review_task.xlsx
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
Filled review file: outputs/my_run/review_task.xlsx
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
     -> 可选 B：tools/run_agent_b_verification.py + tools/run_agent_b_recommendations.py
        -> 可选读取 filled human-review XLSX，生成 notes 分类、无官网标签和回归样例建议
     -> 可选 A 安全应用：tools/apply_agent_optimizations.py --apply
     -> tools/verify_run_outputs.py
```

人工复核后由 Codex 接手：

```text
Codex receives filled manual review workbook
  -> run_review_cycle.sh --update-config
  -> tools/run_review_learning.py
     -> 合并 second-pass 决策和人工填写结果
     -> 重新生成 reviewed final/unresolved
     -> 生成 reviewed/labels.csv
     -> 重新跑 quality gate
     -> 生成 reviewed/learning.md
     -> 对重复出现且安全的排除域名更新 config/scoring.json
     -> tools/build_linked_workbook.py
  -> tools/run_agent_b_recommendations.py
     -> 汇总 AgentB、人工复核 XLSX 和学习报告里的重复模式
  -> tools/apply_agent_optimizations.py --apply
     -> 只应用安全、可解释的 excluded_domains 更新，并写出 human/identity/no_official/reachability 回归样例
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
| `finder/` | 核心逻辑包。包括输入清洗、国家/语言相关搜索 query 构建、API 搜索、网页抓取、官网评分。 |
| `finder/geo.py` | 国家/地区 profile。提供本地语言官网/联系方式查询词、国家 TLD 信号和页面国家文本 marker。 |
| `finder/scoring.py` | 官网打分器。当前版本加入身份 cap：同名/通用名、国家冲突、服务不一致、logo-only、缺少服务或地区佐证时不能直接高分接受；但当页面级名称证据和 marketplace/service 证据同时存在时，会放宽同名/通用名 cap，避免过度拒绝正确官网。 |
| `finder/logo.py` | 从候选官网提取 logo/favicon/og:image，并与 Amazon listing logo 做感知哈希相似度比较；作为正向身份加分证据，但 logo-only 不足以自动接受。 |
| `tools/run_unresolved_second_pass.py` | 对第一轮没解决的商家做二轮补漏，用 Brave/Exa 找更可能的官网；默认接受阈值为 `75`，与 first pass 对齐，同时保留强证据、风险 URL 和身份 cap 约束。 |
| `tools/build_manual_review_task.py` | 生成简化人工复核 CSV/XLSX，只保留工作人员需要判断和填写的列；普通 auto-match 默认复核 75-82 分，second-pass accepted 仍复核 85 分以下。 |
| `tools/run_agent_b_verification.py` | B 的高风险候选优先复核部分。只复核低置信、二轮新增、平台页、logo-only、同名/通用名、身份 cap 等风险行，输出 accept/replace/reject/unsure 和结构化证据。 |
| `tools/run_agent_b_recommendations.py` | B 的建议部分。读取 B 复核结果和人工复核学习报告，输出可执行或需人工评估的优化建议。 |
| `tools/run_review_learning.py` | 读取填好的复核表，合并人工反馈，输出 reviewed 结果、人工标签和优化建议。 |
| `tools/run_agent_c_recommendations.py` | 旧名称兼容入口，内部仍生成 AgentB 建议。 |
| `tools/apply_agent_optimizations.py` | A 的安全应用器，只自动写入可解释、可回滚的 excluded_domains 配置，并生成 human/identity/no_official/reachability 回归样例。 |
| `tools/build_linked_workbook.py` | 生成链接可点击的 XLSX。 |
| `tools/verify_run_outputs.py` | 检查最终 CSV、unresolved CSV、质量 JSON、XLSX 链接公式是否正常。 |
| `tools/evaluate_workflow_balance.py` | 调参评估工具。用基线结果、候选结果和人工标黄复核表计算 false official、over-reject、precision、recall、manual review rows，并按 `review_reason` 输出各人工复核 lane 的负担/风险和“删除该 lane 会漏掉多少已知错误”的模拟；同时模拟 AgentB unresolved recall 候选在不同证据阈值下自动放行会恢复多少正确官网、放出多少错误官网。旧基线目录已清理时，可用 `--labeled-details` 读取已保存的 `balance_eval_details.csv/json` 标签，对当前候选结果重新复算同一组指标。 |
| `tools/build_balance_report.py` | 汇总 100 条有标签评估、300/全量无标签 AgentB 分布和 `simulate_pattern_release.py` 的 pattern-release 结果，生成可重复的阈值、review lane、AgentB recall 是否只能人工处理、以及是否可采用窄口证据组合放行的建议报告。 |
| `tools/build_release_policy_report.py` | 汇总基线/候选有标签评估、pattern-release 模拟和 100/300 条应用结果，生成最终发布策略报告：阈值是否保持、原始 AgentB recall 是否只能人工处理、窄口 pattern release 是否可在风险子域 guard 下启用。 |
| `tools/build_threshold_boundary_report.py` | 汇总阈值模拟、AgentB recall 模拟和 pattern-release 结果，明确全局接受阈值、precision watch 复核分数段，以及哪些放宽只能进入人工/AgentB 证据而不能直接自动接受。 |
| `tools/run_calibration_cycle.py` | 一键生成下一轮校准材料：recall/precision 证据组合报告、recall pattern 放行模拟、protected/spot-check/more-label review lane balance report、selected actionable pattern-release set、均衡 pattern-validation 审核表、空表评估和 cycle summary；可用 `--pattern-release-json` 带入已验证的 pattern-release 模拟，避免下一轮报告和样本丢失已确认策略；也可用 `--filled-sample` 读取填好的样本并汇总 pattern 采纳/拒绝建议，同时输出 `pattern_rule_candidates.json/md`，把候选规则、需更多标签和必须阻断的 pattern 分开。 |
| `tools/mine_evidence_patterns.py` | 证据组合挖掘工具。读取有标签 balance JSON 和 AgentB 证据，找出零错误但仍需更多标签验证的候选规则，以及会释放错误官网的危险组合。 |
| `tools/simulate_pattern_release.py` | 规则放行模拟工具。读取有标签 balance JSON、AgentB 证据和 pattern JSON，计算每个窄 pattern 如果被 A 自动放行，会恢复多少 over-rejected 正确官网、释放多少错误官网，以及 precision/recall/accuracy 的变化；同时区分纯统计安全和可解释的 actionable safe pattern，并输出 selected actionable pattern set，优先选择“身份锚点 + 佐证锚点”的零错误组合；docs/help/support/api/app/login 类子域不会计入可释放候选。 |
| `tools/apply_pattern_release_experiment.py` | 规则放行实验应用器。不会修改默认 workflow，只复制 `official_sites.csv` 并对匹配候选 pattern 的 unresolved 行填入 AgentB candidate，优先使用 selected actionable pattern set，输出实验版 CSV/XLSX，用于再跑有标签评估；同时阻断 docs/help/support/api/app/login 类非官网主页子域。 |
| `tools/apply_pattern_release_to_run.py` | 校准规则正式应用器。读取已验证的 pattern-release JSON 和本次 run 的 `agent_b/check.csv`，只对匹配 selected actionable pattern set 的 unresolved 行写入官网，刷新 `official_sites.csv/xlsx`、`unresolved.csv`、`quality.json`、`review_task.*` 和 manifest，并把释放行保留为 `precision_calibrated_pattern_release` 抽查项；同样阻断 docs/help/support/api/app/login 类子域。 |
| `tools/build_calibration_review_sample.py` | 从大批量 review task 和 AgentB 输出里抽取高价值人工标注样本，优先覆盖 timeout、AgentB reject、风险 lane accept、recall unresolved 和 unsure 行；也可通过 `--pattern-json` 优先抽取证据组合候选规则的验证样本，并用 `--max-per-pattern` 避免单个 pattern 过度占用审核量。 |
| `tools/evaluate_calibration_review_sample.py` | 读取填好的校准样本 CSV/XLSX，按 sample reason、`review_reason` lane、AgentB decision 和 `pattern_match` 汇总人工标签；输出 lane 级保留/降级/继续采样建议，并生成结构化 `pattern_rule_candidates`，供 A 在加回归测试后再决定是否吸收规则。 |
| `tools/apply_review.py` | 人工复核后，把人工 decision 应用回已有 run。 |
| `tests/` | 自动化测试，确保精简或改代码后 workflow 没坏。 |
| `docs/guides/` | 给工作人员看的 PDF 教程。 |

## 最终输出文件

运行成功后，主要看这几个文件：

```text
outputs/my_run/official_sites.csv
outputs/my_run/official_sites.xlsx
outputs/my_run/unresolved.csv
outputs/my_run/quality.json
outputs/my_run/review_task.xlsx
outputs/my_run/review_task.csv
outputs/my_run/agent_b/check.csv
outputs/my_run/agent_b/check.xlsx
outputs/my_run/agent_b/suggestions.md
```

`details/input/`、`details/first_pass/`、`details/second_pass/` 保存中间证据和调试文件。旧版公开文件名仍会生成兼容副本，例如 `provider_final_official_websites_second_pass.csv` 和 `manual_official_site_review_task.xlsx`。

人工复核填完并交给 Codex 后，最终主要看：

```text
outputs/my_run/reviewed/official_sites.csv
outputs/my_run/reviewed/official_sites.xlsx
outputs/my_run/reviewed/unresolved.csv
outputs/my_run/reviewed/learning.md
outputs/my_run/reviewed/labels.csv
outputs/my_run/agent_b/suggestions.md
outputs/my_run/agent_a/applied.json
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
