# 453 个 unresolved 的后续查找最优方案

## 当前分层

基于 `outputs/production_run_brave_twostage_v3/provider_review_sheet_enhanced.csv`：

```text
unresolved total: 453
needs_review: 243
low_confidence: 208
not_found: 2
```

生成的逐行二次查找清单：

```text
outputs/production_run_brave_twostage_v3/unresolved_second_pass_plan.csv
```

最新 Brave+Exa + seed-verification second-pass 结果：

```text
processed_rows: 453
second_pass matched candidates: 195
auto-accepted into second-pass final: 288
final official_url rows after second-pass: 1019
unresolved after second-pass: 165
quality gate: PASS
excluded official URLs: 0
accepted risky URL count: 0
```

主要输出：

```text
outputs/production_run_brave_twostage_v3/unresolved_second_pass_results.csv
outputs/production_run_brave_twostage_v3/unresolved_second_pass_evidence.jsonl
outputs/production_run_brave_twostage_v3/unresolved_second_pass_review_decisions.csv
outputs/production_run_brave_twostage_v3/provider_final_official_websites_second_pass.csv
outputs/production_run_brave_twostage_v3/provider_unresolved_second_pass.csv
outputs/production_run_brave_twostage_v3/provider_official_websites_second_pass_with_clickable_links.xlsx
outputs/production_run_brave_twostage_v3/quality_gate_provider_second_pass_final.md
```

策略分层：

```text
A_verify_top_candidate: 243
B_verify_or_expand_domain_guess: 199
C_broaden_search: 9
D_registry_social_then_manual: 2
```

## 结论

最佳方案不是对 453 行重复首轮搜索，而是用 Brave+Exa 分层处理：

1. 先验证已有 top candidate，尤其是 243 个 `needs_review`。
2. 再验证或扩展 199 个“域名猜测型”低置信候选。
3. 只把 Exa 作为语义补召回，不让它重复跑所有 Brave 精确 query，从而控制成本和速度。

这样成本最低、收益最高，也能保持最终输出质量。

## A 层：243 个 needs_review

这些记录已经有候选官网，只是证据不足以自动 match。当前特征：

```text
平均置信度: 62.0
top candidate 来源: Brave 为主
常见证据: domain_exact_provider_slug, search_result_contains_exact_name, top_search_result
```

处理方式：

```text
目标：把可验证的 needs_review 提升为 matched。
工具：Brave 精确召回 + Exa 语义补召回；必要时 Firecrawl/Tavily/Playwright 做正文增强。
接受条件：候选页面同时出现 provider name + 服务/地点/contact/about 等公司页证据，且不是社交/目录/平台页。
```

推荐命令方向：

```bash
python3 tools/plan_unresolved_second_pass.py \
  --run-dir outputs/production_run_brave_twostage_v3 \
  --output outputs/production_run_brave_twostage_v3/unresolved_second_pass_plan.csv
```

然后按 `strategy_tier=A_verify_top_candidate` 过滤，优先打开或 scrape `top_candidate_url`。

预期收益：这一层最可能批量转正，因为候选域名已经和商家名强相关，只缺正文或动态渲染证据。

## B 层：199 个 verify_or_expand_domain_guess

这些通常是 `providername.com` / `www.providername.com` 的直接域名猜测，当前分数多为 35，说明“域名形态像官网”，但页面证据不够。

处理方式：

```text
目标：验证猜测域名是否真实属于该 provider。
第一步：HTTP 抓首页、/about、/contact、/services。
第二步：用 Firecrawl/Tavily 获取更干净正文。
第三步：用 RDAP/ICANN 只做辅助校验，识别 parked domain、近期注册、明显无关 registrant。
第四步：仍不够时再用 Brave + SerpApi/Serper 扩展查询。
```

接受条件：

```text
域名可访问
页面有 provider name 或高度相似品牌名
页面有 Amazon/ecommerce/服务类别/地点/contact 等至少一个辅助证据
不是 parked domain、域名出售页、directory、social profile
```

预期收益：中等。它们不应该直接自动 match，但很适合作为低成本验证队列。

## C 层：9 个 broaden_search

这些当前 top candidate 分数低，或候选明显弱相关。

处理方式：

```text
目标：重新发现候选，而不是验证现有候选。
工具优先级：Brave 精确 SERP -> Exa semantic search -> SerpApi/Serper Google SERP -> Tavily/Firecrawl scrape。
查询策略：加入国家语言、本地注册号关键词、服务类别、Amazon/Seller Central、site:github.com。
```

预期收益：不确定，需要更宽搜索面和更多人工判断。

## D 层：2 个 not_found

当前所有候选都被排除或不可用。

处理方式：

```text
目标：先找公司身份，再找官网。
工具：OpenCorporates/Wikidata/RDAP/SerpApi/Brave/GitHub search。
输出规则：注册资料、社交平台、目录页只能作为线索，不直接作为 official_url。
```

## 在线资料依据

已调研的资料源和用途：

- [Brave Search API](https://brave.com/search/api/)：独立 web index、Web Search endpoint、Goggles 可做重排/过滤，适合主搜索源。
- [SerpApi Google Organic Results](https://serpapi.com/organic-results)：返回 Google `organic_results`，字段包含 `position/title/link/snippet`，适合作为 Brave 的第二搜索引擎视角。
- [Tavily Search](https://docs.tavily.com/documentation/api-reference/endpoint/search)：支持 `include_raw_content`、`search_depth`、`include_domains/exclude_domains`，适合对候选页做正文验证。
- [Firecrawl Search](https://docs.firecrawl.dev/api-reference/endpoint/search)：Search endpoint 支持 `includeDomains/excludeDomains/scrapeOptions`，适合“搜索 + 抓正文”合并步骤。
- [Exa Search](https://docs.exa.ai/reference/search)：搜索时可提取 `contents.text/highlights`，适合作为 second-pass 语义补召回源。
- [GitHub Code Search syntax](https://github.com/github/docs/blob/main/content/search-github/github-code-search/understanding-github-code-search-syntax.md)：支持 `repo:`、`language:`、`path:` 等 qualifier 和正则；本工作流继续把 GitHub 当线索源，不把 GitHub API 作为主依赖。
- [OpenCorporates API](https://api.opencorporates.com/)：公司资料覆盖超过 2 亿家公司，适合核验法律名称/司法辖区，但不是官网输出源。
- [Wikidata SPARQL Query Service](https://www.wikidata.org/wiki/Wikidata:SPARQL_query_service)：可查询结构化实体和 official website 属性，覆盖率低但命中时精度高。
- [ICANN RDAP](https://www.icann.org/rdap/)：可查询域名注册资料，适合验证/排除候选域名，不适合从公司名反查官网。
- [Common Crawl CDX Index](https://index.commoncrawl.org/)：可查询 URL 历史索引，适合验证候选域名是否长期存在或是否有历史页面。

## 推荐执行顺序

```text
阶段 1：A 层 top candidate 验证
阶段 2：B 层 domain_guess 验证
阶段 3：C/D 层扩展搜索和公司身份核验
阶段 4：人工复核表填 manual_decision / manual_url
阶段 5：apply_review 或 finalize-results 生成新 final
阶段 6：quality_gate + verify_run_outputs
```

已接入工作流的命令：

```bash
make second-pass RUN_DIR="outputs/new_run"
```

或随完整 pipeline 一起执行：

```bash
make pipeline SOURCE_CSV="/path/to/input.csv" RUN_DIR="outputs/new_run"
```

second-pass 的自动接受规则是分段的：`>=85` 自动接受；`70-84` 需要强页面证据或多源支持；`50-69` 必须同时有域名身份和页面/搜索/服务证据；其余只写入 `unresolved_second_pass_results.csv`，不直接进入最终官网列。

## 不建议做的事

```text
不要把 LinkedIn/Facebook/Crunchbase/Trustpilot/目录页直接写为 official_url。
不要让 Exa 重复跑所有普通 query；Exa 默认只跑 3 个 semantic query。
不要用 DDGS 作为唯一生产源；它适合补漏和探索。
不要绕过 quality gate 手工拼最终 CSV。
```
