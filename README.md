# 火星智能客服

一个客服 Agent，按需使用五个 Skills 处理产品咨询、异常排查和需求建议；后台维护会话负责知识沉淀。知识来源是你的 [knowledge 仓库](https://github.com/lxxalalei/knowledge)。

## 使用

需要 Claude Code 和 Python 3.10+。拉取本仓库后，在仓库根目录运行：

```bash
python3 tools/support.py sync
claude
```

Claude Code 直接加载 `CLAUDE.md` 和 `.claude/skills/`。知识库更新后再次运行 `sync`，会同步 `knowledge-source.json` 指定分支的当前内容；每次读取保留来源版本。

正常提问即可使用客服。需要整理已解决案例时，新开一次维护会话，说明“按 curator.md 整理这些案例”；审核和撤回由你明确提出。

## 主要文件

| 文件 | 用途 |
| --- | --- |
| [agent.md](agent.md) | 客服总控与群内人工协作 |
| [.claude/skills/](.claude/skills/) | 五个在线 Skills：意图、咨询、排查、需求、知识检索；另有后台知识沉淀 Skill |
| [curator.md](curator.md) | 后台知识维护角色 |
| [knowledge-source.json](knowledge-source.json) | 知识仓库与分支 |
| [tools/support.py](tools/support.py) | 知识同步、检索、需求与案例存储 |
| [docs/tools.md](docs/tools.md) | 工具命令与字段 |

运行数据统一放在 `data/`：知识缓存位于 `knowledge/`，需求位于 `requirements/`，知识候选位于 `curation/`。候选批准后才能被客服检索。`data/` 不进入 Git；源码迁移靠 Git，已有业务记录按需另外保留。

## 验证

```bash
python3 -m unittest discover -s tests -v
```

自己试用时，重点核对：答案有知识正文依据；排查无依据时交给同群产品同事；明确要求保存后确有需求记录；案例没有恢复证据时不入库，未审核时不进入检索。
