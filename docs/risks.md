# 风险登记

| ID | 风险 | 阶段 | 缓解 | 状态 |
|---|---|---|---|---|
| R-001 | 把旧项目完成状态错误继承到新项目 | R0 | 新状态统一为 `not_implemented_in_new_repo`，比较器不把缺失算通过 | 已缓解 |
| R-002 | 兼容测试绑定旧实现偶然细节 | R0–R10 | R0 先保存能力证据；目标阶段仅冻结调用者可见行为与安全不变量 | 持续 |
| R-003 | 旧工作区脏状态污染基线 | R0 | 固定 commit，LegacyDriver 拒绝 dirty checkout | 已缓解 |
| R-004 | 复制旧代码破坏绿地架构 | 全程 | 逐文件复用登记、源码标记和依赖扫描 | 持续 |
| R-005 | Fake 测试被误当真实质量 | R2–R10 | 报告区分 Fake、真实基础设施、真实 Provider 和 `not_comparable` | 持续 |
| R-006 | LangChain/LangGraph 类型污染领域 | R1–R10 | Domain 依赖门禁；框架只在 Adapter/Orchestration | 持续 |
| R-007 | 43 个 R0 evidence probe 粒度不足 | R1–R10 | 每个目标阶段将其深化为具体黑盒场景，R10 阻断未深化能力 | 持续 |

