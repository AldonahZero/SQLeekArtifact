# SQLeek 多模型对比实验

这个目录用于比较 SQLeek 在不同 OpenAI-compatible 模型上的表现。默认模型配置包含 DeepSeek V4 Flash、GPT-5.5 (medium) 和 Qwen 27B；实际部署名和服务地址必须按你使用的供应商/本地 vLLM 服务覆盖，不能只依赖配置中的示例别名。

## 实验设计

每个 `model × DBMS × repeat` 都有独立目录，目录中保存：

- `llm_usage.jsonl`：每次 LLM 请求的 provider-reported prompt/completion/total tokens；
- `stage2/`：该模型生成的 seeds；
- `fuzz/`：模糊测试输出；
- `metadata.json`、`runner.log`、`result.json`：运行元数据和最终统计。

运行器会设置 `SQLEEK_LLM_DBMS`，因此当前 DBMS 之外的 Stage 2 请求不会混入 Token 总量。Bug 数必须来自去重后的报告（优先使用 Stage 4 合并后的 `crash_report.json`），不会把 crash 总数当成 Bug 数，也不会把缺失报告当成 0。

## 配置模型服务

以 `models.json` 中的环境变量名为准。例如：

```bash
export SQLEEK_DEEPSEEK_V4_FLASH_API_KEY='...'
export SQLEEK_DEEPSEEK_V4_FLASH_BASE_URL='https://你的兼容接口/v1'
export SQLEEK_DEEPSEEK_V4_FLASH_MODEL='你的实际部署名'

export SQLEEK_GPT_5_5_MEDIUM_API_KEY='...'
export SQLEEK_GPT_5_5_MEDIUM_BASE_URL='https://api.openai.com/v1'
export SQLEEK_GPT_5_5_MEDIUM_MODEL='你的实际部署名'

export SQLEEK_QWEN_27B_API_KEY='...'
export SQLEEK_QWEN_27B_BASE_URL='http://你的vllm服务:8001/v1'
export SQLEEK_QWEN_27B_MODEL='你的实际部署名'

# Optional: populate Cost (USD) using per-million input/output rates.
export SQLEEK_DEEPSEEK_V4_FLASH_INPUT_USD_PER_MILLION='...'
export SQLEEK_DEEPSEEK_V4_FLASH_OUTPUT_USD_PER_MILLION='...'
export SQLEEK_GPT_5_5_MEDIUM_INPUT_USD_PER_MILLION='...'
export SQLEEK_GPT_5_5_MEDIUM_OUTPUT_USD_PER_MILLION='...'
export SQLEEK_QWEN_27B_INPUT_USD_PER_MILLION='...'
export SQLEEK_QWEN_27B_OUTPUT_USD_PER_MILLION='...'
```

不要把 API key 写入 `models.json`、`metadata.json` 或提交到 Git。

## 先做 dry-run

```bash
cd /Users/aldno/paper/Sqleek/SQLeek/experiment/otherLLMs
python3 run_multi_model.py \
  --repo-root /Users/aldno/paper/Sqleek/SQLeek \
  --models deepseek_v4_flash,gpt_5_5_medium,qwen_27b \
  --dbms postgres,mysql,mariadb,monetdb \
  --repeats 5 \
  --duration 24h \
  --dry-run
```

正式运行时去掉 `--dry-run`。默认命令是：

```text
bash {repo_root}/run.sh {dbms} {run_id} {duration}
```

如果你的集群需要自己的启动器，可以替换命令；占位符见 `run_multi_model.py` 文件头。例如：

```bash
python3 run_multi_model.py \
  --models deepseek_v4_flash,gpt_5_5_medium,qwen_27b \
  --dbms postgres --repeats 5 --duration 24h \
  --command 'bash {repo_root}/run.sh {dbms} {run_id} {duration}' \
  --post-command 'python3 你的去重脚本 --run-dir {run_dir} --output {run_dir}/triage/crash_report.json' \
  --bug-report '{run_dir}/triage/crash_report.json'
```

`--post-command` 会在模糊测试命令后执行，适合调用现有的 Bug 去重/复现脚本；它与主命令共享同一组占位符和隔离环境。`--bug-report` 指向的文件必须由该 triage 步骤写入；如果报告在运行目录下且文件名为 `crash_report.json`/`bug_report.json`，可以省略该参数让运行器自动发现。只生成 seeds、检查 Token 记录时使用 `--stage2-only`。

## 汇总 Token、Cost 与 Bug 的关系

空结果表模板已经准备好：

- [result_table_template.csv](./result_table_template.csv)：按模型和 DBMS 的汇总表；
- [run_level_token_bug_template.csv](./run_level_token_bug_template.csv)：逐次重复实验表；
- [discussion_table_template.tex](./discussion_table_template.tex)：可直接放入 Discussion 的 LaTeX 表格，结果单元格暂留为 `\TODO{XX}`。

在每个 run 完成并且 Bug 报告可读后执行：

```bash
python3 analyze_results.py \
  --input results/results.jsonl \
  --output-dir results/analysis

# 可选：需要 matplotlib
python3 plot_token_bug_tradeoff.py \
  --input results/analysis/run_level_token_bug.csv \
  --output results/analysis/token_bug_tradeoff.png
```

汇总表重点看：

- `bugs_per_10k_tokens`：Token 归一化后的 Bug 产出；
- `tokens_per_bug`：发现一个去重 Bug 的 Token 成本；
- `mean/median/min/max_bug_count` 与 `bug_count_cv`：模型更换或重复运行时是否剧烈波动；
- `pearson/spearman_tokens_vs_bugs`：同一模型的 run-level Token–Bug 关系。少于 3 个有效重复或无变异时会留空，不对单次运行强行下结论。

论文 Discussion 中应同时报告原始 Bug 数、Total Tokens 和 Cost (USD)，并按 DBMS 展开；只报告某一个模型的总数或只报告总 Token 都不足以证明方法优势。

如果要和现有 SQLeek 的 45 个去重 Bug 做对照，应把原方法也作为一条独立 baseline run 记录，并填入它实际测得的 Token 日志；不要只把 `45` 硬编码进汇总表，否则无法分析 Token–Bug 关系。
