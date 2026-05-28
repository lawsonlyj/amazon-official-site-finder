# Amazon 官网识别工作流用户手册

## 适用输入

输入是同格式 Amazon GSPN/SPN 服务商 CSV。至少应包含 `provider_id`、`provider_name`、`detail_url`、`listing_logo_url`、`about_listing_text`、`service_api`、`service_types_json`、`provider_locations_json`、`provider_languages_json` 等字段。

## 一次性安装

```bash
git clone https://github.com/lawsonlyj/amazon-official-site-finder.git
cd amazon-official-site-finder
cp .env.example .env
```

编辑 `.env`，填入：

```bash
BRAVE_API_KEY=...
EXA_API_KEY=...
```

推荐同时配置 Brave 和 Exa。Brave 做精确搜索，Exa 做 second-pass 语义补召回。

## 运行

```bash
./run_workflow.sh "/path/to/provider_details.csv" "outputs/my_run"
```

脚本会自动安装可选依赖、检查 API、运行首轮搜索、运行 second-pass、生成 CSV/XLSX 并校验输出。

## 主要输出

```text
outputs/my_run/provider_final_official_websites_second_pass.csv
outputs/my_run/provider_official_websites_second_pass_with_clickable_links.xlsx
outputs/my_run/provider_unresolved_second_pass.csv
outputs/my_run/unresolved_second_pass_results.csv
outputs/my_run/unresolved_second_pass_evidence.jsonl
outputs/my_run/quality_gate_provider_second_pass_final.json
outputs/my_run/manifest.json
```

交付给业务方优先用：

```text
provider_final_official_websites_second_pass.csv
provider_official_websites_second_pass_with_clickable_links.xlsx
```

## 质量校验

```bash
python3 tools/verify_run_outputs.py \
  --run-dir "outputs/my_run" \
  --final provider_final_official_websites_second_pass.csv \
  --unresolved provider_unresolved_second_pass.csv \
  --quality quality_gate_provider_second_pass_final.json \
  --xlsx "outputs/my_run/provider_official_websites_second_pass_with_clickable_links.xlsx"
```

通过条件包括：行数一致、每行保留 Amazon 原始详情页链接、URL 格式正常、排除域没有进入官网列、XLSX 链接公式无错误。

## 人工抽样

生产交付前建议抽样检查：

- `precision_second_pass_accepted_lt70`：优先查，确认自动接受的官网是否正确。
- `precision_second_pass_accepted_70_84`：中置信度抽查。
- `precision_first_pass_auto_matched`：首轮自动匹配抽查。
- `recall_unresolved_top_candidate`：查剩余 unresolved 是否有可手动接受的官网。

抽样表的填写方式：

```text
manual_decision = accept / replace / reject / unsure
manual_url      = 正确官网；replace 时必填
reviewer_notes  = 错误原因或判断依据
```

## Codex skill 用法

安装 skill 后，可以对 Codex 说：

```text
用 Amazon Official Site Finder 跑这个输入文件，输出到 outputs/my_run，并解释质量结果。
```

或：

```text
检查 outputs/my_run 的 unresolved，告诉我哪些最值得人工复核。
```

Skill 会调用 repo 里的脚本，不会替代 API key；`.env` 仍需本地配置。

## 发布到 GitHub

本地确认测试通过后：

```bash
git add .gitignore README.md USER_MANUAL_CN.md RUNBOOK_CN.md REPRODUCIBILITY_CN.md WORKFLOW_CN.md UNRESOLVED_NEXT_STEPS_CN.md Makefile run_workflow.sh requirements-optional.txt config finder tools tests codex-skills .github
git commit -m "Initial Amazon official site finder workflow"
gh repo create lawsonlyj/amazon-official-site-finder --private --source=. --remote=origin --push
```

是否 public/private 由仓库 owner 决定。不要提交 `.env`、`.cache/`、`.vendor_eval/`、`outputs/` 或真实客户输入文件。
