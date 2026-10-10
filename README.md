# Mars Customer Service Agent（开发项目）

面向企业 IM「火星」的智能客服 Agent 配置与测试项目。当前采用**单 Agent 自主决策**方案，优先解决产品功能咨询和使用异常，不引入多 Agent 编排。

> 当前状态：**配置已编写、知识库为模拟数据，尚未接入公司 KMS、在线表格、OpenViking 或工单系统，也尚未在公司 Claude Runtime 中完成回归测试**。

## 双仓库分工

| 位置 | 用途 |
| --- | --- |
| [my_server（本仓库）](https://github.com/lxxalalei/my_server) | Agent 总控、Skills、测试用例 |
| [knowledge](https://github.com/lxxalalei/knowledge) | **虚构演示知识**；不代表真实火星产品行为 |
| 公司 KMS（未来适配） | 真实的 Markdown 产品知识及操作规则 |
| 公司在线表格（未来适配） | 结构化已知问题、适用版本、解决状态等 |
| OpenViking（按需） | 云 Agent 平台提供的共享经验与索引补充能力 |

## 项目文件

- [agent.md](agent.md)：主 Agent 总控 v0.1。负责理解诉求、切换流程、持续跟进与兜底。
- [knowledge-search](skills/knowledge-search/SKILL.md)：统一知识检索 v0.1。控制何时查、查哪里、如何核对依据。
- [faq-support](skills/faq-support/SKILL.md)：使用咨询 v0.1。组织操作说明、功能和权限回答。
- [issue-support](skills/issue-support/SKILL.md)：使用异常 v0.1。持续排查并据反馈更新判断。
- [知识检索用例](tests/knowledge-search-cases.md)：12 条知识检索与事实边界测试。
- [客服端到端用例](tests/customer-service-cases.md)：12 条单轮场景、3 组多轮脚本，**均待公司 Claude Runtime 验证**。

## 第一轮测试方式

1. 在支持读取 GitHub 文件的 Agent 运行环境中加载 `agent.md`，绑定三个 Skill 及实际 GitHub 读取工具。
2. 使用 [knowledge](https://github.com/lxxalalei/knowledge) 作为**演示数据源**，并明确所有答案仅代表 DEMO-v0.1。
3. 对照 [客服端到端用例](tests/customer-service-cases.md) 逐条提问；要求 Agent 实际读取所需知识正文。
4. 记录实际回答、使用的知识依据和是否通过；优先修复事实编造、重复追问、错误解决状态。
5. 完成模拟测试后，再评估如何用公司 KMS CLI / 在线表格 CLI 替换 GitHub 读取工具。

**注意**：将 `SKILL.md` 放在仓库里，并不等于云 Agent 平台已经自动安装、加载或授予对应工具权限。要以公司平台的 Agent Instructions、Skills 与 CLI 接入规则为准。

## 正式部署注意事项

- 获取公司 KMS/在线表格 CLI 的工具说明与结果字段后，再编写实际适配，不预先编造命令。
- 正式知识以 KMS/在线表格为准；是否需要 OpenViking 做二次索引，以测试效果决定。
- 禁止提交公司内部资料、密钥、真实员工咨询原文等内容到公开仓库。
