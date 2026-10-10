# 本地支持工具

工具命令统一返回 JSON；失败返回 `{"ok":false,"error":{"code":"...","message":"..."}}` 并以非零退出。默认数据目录是仓库根目录下的 `data/`，可用 `--data-dir DIR` 指定绝对或相对目录：

```text
python3 tools/support.py [--data-dir DIR] <command> [options]
```

## 知识同步与读取

根目录 `knowledge-source.json` 只需要包含：

```json
{"repo":"owner/name","ref":"main"}
```

`sync` 通过 GitHub API 查询 `ref` 当前实际 commit，读取该 commit 的递归 tree，收集全部 Markdown，再从 raw URL 下载。tree 被截断、文件下载失败、UTF-8 无效或 Git blob hash 不一致时同步失败；所有文件成功后才替换 `data/knowledge/`，既有缓存仍可用。同步记录 `repo`、`ref`、`commit`、路径、来源 URL 和 hash。同步不会覆盖本地需求或候选记录。

```text
sync
search --query TEXT
read --path RELATIVE
capabilities
```

`search` 只读取本地缓存，按大小写不敏感的文本关键词匹配；空格分隔的词要求同时出现。返回 `path`、`title`、`type`、`excerpt`、来源和 `body_read:false`。根 `README.md` 可用 `read` 读取，类型为 `index`，不会作为产品/案例搜索结果。`cases/` 路径类型为 `case`，其他 Markdown（除 README）类型为 `product`。未同步、缓存缺失或缓存损坏会明确失败，不能用空结果代替。

## 需求记录

```text
requirement-save --input FILE
requirement-read --id ID
```

输入 JSON 必须有 `request_id`、`summary`、`scenario`、`desired_outcome`，可选 `impact`、`workaround`、`source_refs`。`request_id` 是安全文件名。记录写入 `data/requirements/<request_id>.json`，返回 `status:"saved"`、`storage:"local"`、`idempotent` 和 `written`。相同 ID 且规范化内容相同会幂等返回，内容不同返回 `REQUEST_CONFLICT`；不会调用外部工单或提交系统。

## 候选案例维护

候选输入必须有 `candidate_id`、`title`、`symptom`、`environment`、`resolution`、`resolution_evidence`、非空 `source_refs` 数组，并且 `resolved:true`。命令为：

```text
curation-save --input FILE
curation-read --id ID
curation-list [--query TEXT]
curation-review --id ID --decision approve|reject --note TEXT
curation-withdraw --id ID --note TEXT
```

记录写入 `data/curation/<candidate_id>.json`，状态为 `pending_review`、`approved`、`rejected` 或 `withdrawn`。`curation-list` 可列出所有状态，返回必要摘要和 `body_read:false`；关键词零命中只代表当前文本匹配为空，不代表语义去重完成。`curation-read` 可读所有状态。

审核会更新本地记录状态。`pending_review` 可批准或拒绝；终态重复同判定幂等，不能改成另一终态。只有 `approved` 可撤回；`withdrawn` 重试幂等，`pending_review` 和 `rejected` 撤回失败。已批准案例作为并行 `case` 搜索来源，并可通过 `read --path curated/<id>.json` 读取；待审、拒绝、撤回案例不会进入在线搜索或正文读取。原始 `resolution`、`resolution_evidence` 和审核备注在撤回时保留。

## 数据与边界

```text
data/
  knowledge/manifest.json  # 同步来源与文件元数据，正文同目录
  requirements/            # 本地需求 JSON
  curation/                # 本地候选/审核 JSON
  events.jsonl             # 精简命令成功/失败、来源、路径或 ID 记录
```

`source_refs` 保存候选案例的事实来源。状态文件损坏、路径越界、缺少记录或无效输入会返回明确错误。
