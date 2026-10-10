# 客服 Agent 模拟对话核对 v0.1

> 日期：2026-10-11  
> 性质：**基于仓库文档的人工静态推演，不是 Claude/Multica Runtime 的独立执行结果；不得计入正式测试通过率。**  
> 资料：[knowledge 模拟库](https://github.com/lxxalalei/knowledge)；内容均为虚构的 DEMO-v0.1 产品规则。

本文件用于快速核对三个 Skill 与已有知识文档的设计是否自洽。真正评测时，请按 [端到端测试集](customer-service-cases.md) 逐项调用实际 Agent 与文件读取工具，记录其原始输出。

## 样例 1：权限型 FAQ

**用户：** 演示版普通成员如何修改群名称？

**已核对文档：** [product/group-chat.md](https://github.com/lxxalalei/knowledge/blob/main/product/group-chat.md)

**符合规则的示范回答：**
> 演示版的规则是只有群主和管理员能修改群名称，普通成员不能直接修改。可以请群主或管理员打开群聊，进入“群设置 → 群名称”进行修改。

**核对点：** 明确适用 DEMO；依据权限规则回答；不无意义追问客户端。  
**静态结论：** 文档足以支持该回答。

## 样例 2：同一问题转入排查

**用户：** 我是管理员，但在群设置中找不到群名称入口。

**已核对文档：** [product/group-chat.md](https://github.com/lxxalalei/knowledge/blob/main/product/group-chat.md)

**符合规则的示范回答：**
> 按演示版规则，管理员应当有修改权限。你已经进入了群设置，先确认一下你现在使用的是 Windows、Android 还是 iOS？我再根据终端核对下一步。

**核对点：** 不再询问是否管理员；不直接认定是 Bug；从 FAQ 转向排查。  
**静态结论：** 文档支持“权限应当具备”，但**缺少更多诊断信息**，需要追问。

## 样例 3：排除图片大小原因

**用户第一轮：** 演示版 Windows 端文字能发，图片一直转圈。

**示范回复：**
> 文字可以正常发送，问题目前集中在图片发送。演示版文档规定单张图片上限为 10 MB，你这张图片大约多大？

**用户第二轮：** 200 KB。

**示范回复：**
> 200 KB 没有超过演示版的 10 MB 限制，因此目前不应按文件过大处理。是所有图片都发不出去，还是只有这一张？

**已核对文档：** [product/messaging.md](https://github.com/lxxalalei/knowledge/blob/main/product/messaging.md)、[cases/image-send-failure.md](https://github.com/lxxalalei/knowledge/blob/main/cases/image-send-failure.md)

**核对点：** 利用“文字正常”事实；根据新数据排除文件过大；继续缩小范围；未声称解决。  
**静态结论：** 文档与处理流程一致。

## 样例 4：知识缺失

**用户：** 演示版支持消息定时发送吗？

**已核对文档：** [product/messaging.md](https://github.com/lxxalalei/knowledge/blob/main/product/messaging.md)

**符合规则的示范回答：**
> 目前这份演示版产品资料没有说明是否支持消息定时发送，因此我还不能确认这个功能是否可用。

**核对点：** “没有文档依据”不等于“不支持”。  
**静态结论：** 文档明确要求保留不确定性。

## 核对结果与后续

- **文件一致性**：四个示范场景都能从现有模拟知识得到明确依据或明确的不确定性边界。
- **仍未验证**：Claude 是否会真实加载 Skills、调用 GitHub/KMS CLI、按顺序检索、在多轮中稳定维持状态，以及真实问题解决率。
- **下一步**：在已配置的 Claude Agent Runtime 中按正式测试集进行真实运行，逐条记录原始回复与工具调用轨迹。
