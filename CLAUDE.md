# 火星客服工作区

@agent.md

Skills 位于 `.claude/skills/`，按任务加载对应 Skill。

用户明确发起后台知识整理、审核或撤回时，读取 `curator.md`，按后台维护角色处理；普通客服咨询不自动维护知识。

工具从仓库根目录运行：`python3 tools/support.py`。首次使用先读取 [工具说明](docs/tools.md)；知识尚未同步时执行 `sync`。
