# 项目目录说明

这个仓库的正式结构按“可执行代码、配置、文档、测试、Codex skill”分组。`outputs/`、`.cache/`、`.vendor_eval/`、`evidence/` 是本地运行产物或缓存，不进入正式交付目录。

## 顶层目录

```text
amazon-official-site-finder/
  README.md
  Makefile
  .env.example
  requirements-optional.txt
  run_workflow.sh
  run_codex_assisted.sh
  config/
  finder/
  tools/
  tests/
  codex-skills/
  docs/
```

## 目录职责

| 路径 | 用途 |
|---|---|
| `README.md` | 项目入口、快速运行命令、核心说明。 |
| `.env.example` | API key 和运行参数模板；真实 `.env` 不提交。 |
| `run_workflow.sh` | 标准一键运行入口：输入 CSV 到最终 CSV/XLSX。 |
| `run_codex_assisted.sh` | Codex skill 使用的一键入口：从 key 文件生成 `.env` 后运行 workflow。 |
| `config/` | 评分、排除域、动态抓取等配置。 |
| `finder/` | 核心 Python 包：输入标准化、query 构建、搜索源、抓取、评分、最终化。 |
| `tools/` | 可执行辅助脚本：pipeline、second-pass、质量门禁、XLSX、人工复核、key 文件配置。 |
| `tests/` | 单元测试和 fixture。 |
| `codex-skills/` | 可安装到 Codex 的操作助手 skill。 |
| `docs/` | 面向用户和交接的文档、PDF 教程、调研记录。 |

## docs 目录

```text
docs/
  PROJECT_STRUCTURE_CN.md
  guides/
    amazon_official_site_finder_user_guide_cn_20260528.pdf
    amazon_official_site_finder_codex_skill_guide_cn_20260528.pdf
  cn/
    REPRODUCIBILITY_CN.md
    RUNBOOK_CN.md
    USER_MANUAL_CN.md
    WORKFLOW_CN.md
    UNRESOLVED_NEXT_STEPS_CN.md
  research/
    TOOL_EVALUATION_CN.md
    TOOL_SELECTION_MATRIX.csv
```

| 路径 | 用途 |
|---|---|
| `docs/guides/` | 给非开发者或 Codex 用户看的 PDF 教材。 |
| `docs/cn/REPRODUCIBILITY_CN.md` | 可复现交付说明。 |
| `docs/cn/RUNBOOK_CN.md` | 运行、排查、复核 runbook。 |
| `docs/cn/WORKFLOW_CN.md` | 工作流设计和历史方案说明。 |
| `docs/cn/USER_MANUAL_CN.md` | 用户操作手册。 |
| `docs/cn/UNRESOLVED_NEXT_STEPS_CN.md` | unresolved 和 second-pass 后续策略。 |
| `docs/research/` | 工具调研、API 选择和试跑记录。 |

## 本地运行产物

这些目录由脚本运行生成或缓存，已在 `.gitignore` 中排除：

```text
outputs/
evidence/
.cache/
.vendor_eval/
.spreadsheet_build/
```

正式交付时一般只需要输出目录中的：

```text
provider_final_official_websites_second_pass.csv
provider_official_websites_second_pass_with_clickable_links.xlsx
provider_unresolved_second_pass.csv
quality_gate_provider_second_pass_final.json
```
