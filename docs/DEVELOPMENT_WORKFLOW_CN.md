# 开发工作流流程

本文档只给维护者使用。普通用户复用 GitHub 仓库时，应运行 README 中的“Workflow Body”，不需要运行本页的开发流程。

## 两个流程的边界

### 工作流主体

工作流主体是最终给别人复用的稳定部分：

```text
输入 provider CSV
  -> 搜索候选
  -> 评分
  -> second-pass 补漏
  -> 输出 official_sites / unresolved / review_task
  -> 人工复核后输出 reviewed
```

特点：

- 规则化、结构化、可重复。
- 默认不使用自主 LLM agent。
- 不需要 OpenAI key。
- 普通工作人员只需要提供 API key 文件、输入 CSV 和输出目录。

### 开发工作流流程

开发工作流流程是维护者用来继续优化工作流主体的过程：

```text
Operation and Optimization 跑工作流主体
  -> Check and Suggestion 复核高风险行并提出建议
  -> 人工提供少量高价值标签
  -> Operation and Optimization 只吸收安全、可解释、可测试的规则
  -> deterministic gate 验证指标、回归样例和测试
  -> 合入工作流主体
```

特点：

- 用来开发、校准、验证规则。
- 可以接入真正的 CheckAgent / OptimizationAgent，但 agent 只参与判断和建议。
- agent 输出不能直接改生产结果或配置。
- 任何规则吸收都必须经过测试、回归样例和指标门禁。

## 当前命名

- **Operation and Optimization**：运行主流程，并在开发阶段应用安全优化。
- **Check and Suggestion**：只复核高风险行，收集 DOM/证据，提出建议。

历史脚本名仍保留 `agent_b`、`agent_c`、`agent_optimizations`，只是为了旧命令兼容。

## 普通用户不要默认运行的内容

以下内容属于开发工作流流程，不是工作流主体的必需步骤：

- `--run-check-suggestion`
- `--apply-operation-optimizations`
- `--pattern-release-json`
- `tools/run_calibration_cycle.py`
- `tools/build_balance_report.py`
- `tools/simulate_pattern_release.py`
- `tools/build_policy_validation_task.py`
- `tools/check_calibration_application_gate.py`

这些工具用于判断规则是否应该调整，而不是普通批量输出官网的默认流程。

## 开发流程命令

先跑一批样本的工作流主体，并额外启用高风险复核：

```bash
./run_codex_assisted.sh \
  --brave-key-file "/path/to/brave_key.txt" \
  --exa-key-file "/path/to/exa_key.txt" \
  --source "/path/to/provider_details.csv" \
  --run-dir "outputs/dev_run" \
  --run-check-suggestion
```

如果有人工复核文件：

```bash
./run_review_cycle.sh \
  "outputs/dev_run" \
  "/path/to/filled_review_task.xlsx" \
  --update-config
```

如果已有经过验证的 pattern release JSON，才可以显式应用：

```bash
./run_workflow.sh \
  "/path/to/provider_details.csv" \
  "outputs/dev_run" \
  --run-check-suggestion \
  --pattern-release-json "outputs/calibration/pattern_release_simulation.json"
```

## 开发输出

开发流程会在工作流主体输出之外写入：

```text
outputs/dev_run/check_suggestion/check.csv
outputs/dev_run/check_suggestion/check.xlsx
outputs/dev_run/check_suggestion/suggestions.json
outputs/dev_run/check_suggestion/suggestions.md
outputs/dev_run/operation_optimization/applied.json
outputs/dev_run/operation_optimization/identity_cases.csv
outputs/dev_run/operation_optimization/human_cases.csv
outputs/dev_run/operation_optimization/no_official_cases.csv
outputs/dev_run/operation_optimization/reachability_cases.csv
```

这些文件用于学习和回归，不是普通交付文件。

## 建议吸收原则

Operation and Optimization 只能自动吸收以下安全内容：

- 重复出现的通用坏域名或平台/目录域名，写入 `excluded_domains`。
- 人工标签生成的回归样例。
- no-official、identity、reachability 等可回滚 fixture。

不能自动吸收：

- 单条商家个案。
- 没有回归样例的阈值调整。
- 会扩大自动接受范围但没有人工标签验证的规则。
- 只靠 agent 判断、没有结构化证据和测试的修改。

## 未来真实 Agent 接入边界

如果以后接入真正意义上的 agent，建议只放在开发工作流流程中：

```text
工作流主体输出结构化证据
  -> CheckAgent 判断高风险行并提出建议
  -> OptimizationAgent 判断建议是否值得改规则
  -> deterministic gate 决定是否允许应用
```

CheckAgent 应只读取：

- 候选 URL
- DOM/JSON-LD/schema.org evidence
- provider name / country / service / Amazon provider_detail_url
- 现有评分和 review_reason

CheckAgent 输出：

- `accept` / `replace` / `reject` / `unsure`
- `confidence`
- `supporting_facts`
- `counter_evidence`
- `reason_for_unsure`
- 可选优化建议

OptimizationAgent 输出：

- 是否建议修改规则
- 修改规则的证据
- 需要补哪些人工标签
- 需要新增哪些回归测试
- 是否阻断本次建议

最后仍由 deterministic gate 执行，不允许 agent 直接改默认生产规则。

## 发布到 main 的原则

推到 GitHub main 的应该是：

- 已经验证过的工作流主体。
- 已吸收进配置或代码的稳定规则。
- 对应回归测试。
- 清晰的普通用户 README。

不应该要求普通用户：

- 理解 CheckAgent / OptimizationAgent。
- 提供 OpenAI key。
- 运行 calibration cycle。
- 运行 Check and Suggestion 才能得到官网输出。
