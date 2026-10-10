# Mars Customer Service Agent（开发项目）

面向企业 IM「火星」的智能客服 Agent 配置与测试项目。当前采用**单 Agent 自主决策**方案，优先解决功能咨询和使用异常，不追求复杂多 Agent 编排。

> 当前状态：**配置与模拟验证阶段，未连接公司 KMS、在线表格、OpenViking 或工单系统，也未在公司 Claude Runtime 中完成回归测试**。

## 代码与知识分离

| 位置 | 职责 |
| --- | --- |
| [本仓库 `my_server`](https://github.com/lxxalalei/my_server) | Agent 总控、Skills、测试标准 |
| [`knowledge` 仓库](https://github.com/lxxalalei/knowledge) | 虚构模拟产品知识库，**非正式产品资料** |
| 公司 KMS（未来） | 实际 Markdown 产品知识及操作规则 |
| 公司在线表格（未来） | 已知问题、版本、状态等结构化数据 |
| OpenViking（可选） | 平台提供时，可辅助索引和历史经验检索 |

## 当前文件

- [agent.md](agent.md)：主 Agent 总控指令 v0.1。
- [skills/knowledge-search/SKILL.md](skills/knowledge-search/SKILL.md)：跨知识来源的检索方法 v0.1。
- [tests/knowledge-search-cases.md](tests/knowledge-search-cases.md)：模拟知识库检索测试用例。

`faq-support` 与 `issue-support` 仍待实现；主 Agent 的处理方法先由 `agent.md` 约束。

## 如何做第一轮模拟测试

1. 在有 GitHub 仓库读取能力的 Agent 环境中，加载 `agent.md` 并绑定 `knowledge-search` Skill。
2. 明确该轮使用 [knowledge](https://github.com/lxxalalei/knowledge) 仓库的 **DEMO** 资料，不作真实火星产品咨询。
3. 按 [检索测试用例](tests/knowledge-search-cases.md) 逐条提问，检查是否实际打开来源、是否错误推断、是否正确继承上下文。
4. 记录失败原因，再修订指令或知识内容。**仅仅把文件放到 GitHub 不会自动安装 Skill 或给 Agent 增加工具权限。**

## 正式部署待办

- 核实公司平台读取 `agent.md` 的具体方式（或复制到 Agent Instructions）。
- 依据 Multica/内部平台配置实际绑定 Skill。
- 取得 KMS、在线表格 CLI 的可用命令、参数、权限与返回字段，适配真实检索。
- 用脱敏、经授权的真实咨询替换模拟用例，进行 Claude 环境回归测试。

安全提醒：本项目仓库目前公开。禁止提交公司内部知识、私人咨询记录、CLI 凭据或内部接口密钥。
