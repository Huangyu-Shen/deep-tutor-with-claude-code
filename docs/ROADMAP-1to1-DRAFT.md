# 路线图草案:逐模块 1:1 复刻 DeepTutor(只换推理)

> 状态:**已确认(2026-11-22)并写入 `CLAUDE.md` 和 `docs/DESIGN.md` 第 6 节**;第 5 节的 2、4、5 点仍未决,已在 `DESIGN.md` 里记为"未决"。
> 证据等级:记忆系统已读源码并写成行为清单([docs/upstream-behavior/memory.md](upstream-behavior/memory.md))。**其余模块只依据目录名和 README 的功能列表,没有读源码**,所以"形态"和"工作量"都是估计,读过源码后会改。

## 1. 目标与已定决定

目标:功能上 1:1 复刻 DeepTutor,与原项目的区别只有一条——推理由 Claude Code 完成,不再自己配置大模型。

你已经定下的决定(来自上一轮追问):
1. 形态仍是 Claude Code 插件(技能加脚本),按原项目功能逐个重写。
2. 需要其他模型的功能(向量检索、图像/视频/语音生成、TTS/STT)暂不做,保持"不调用其他模型 API"。
3. 验收:每个上游模块一份"逐文件行为清单"(源码文件加行号),确定性部分用黄金向量,提示词驱动的行为按清单逐条跑真实会话。
4. 已有的更严格偏离(错题连续答对 2 次、路径门槛三连对、引文逐字核对、教学档案要原话)保留,并在清单里标"有意偏离"。
5. 第一组复刻:记忆系统。记忆整合由用户显式命令触发,技能收尾时提示。

## 2. 上游模块盘点与处置提案

依据:`DeepTutor-main/deeptutor/` 目录清单与 README 的 Key Features。"待读"= 还没读源码。

| 上游模块 | 做什么(目录名/README 推断) | 处置提案 | 状态 |
|---|---|---|---|
| `services/memory`、`capabilities/mastery/memory.py` | 三层记忆;辅导会话记忆 | **复刻**,0.5.0 | 前者已读,后者待读 |
| `learning/`(`assessment`、`semantic_assessment`、`grading`、`lesson_evidence`、`navigation`、`objective_relations`、`outline_revision`、`pending`、`question_card`、`visual_practice`、`topic_*`、`policy`、`service` 等) | 学习路径与评估的其余部分 | **复刻**,逐个读;已移植 `mastery`/`scheduler`/`models`/`policy` 的一部分/`teaching` | 已有部分,其余待读 |
| `services/learning_journal`、`services/persona`、`multi_user/learner_profile` | 学习日志、人设、学习者画像(含入门画像) | **复刻**(去掉多用户部分) | 待读 |
| `services/practice`(`analytics`、`answers`、`importing`、`scheduler`、`storage`) | 练习与题库的分析、导入 | **复刻**,与已有题库对齐 | 待读 |
| `agents/chat`、`agents/loop`、`agents/_shared` | 对话主循环与共享逻辑 | **换形态**:Claude Code 本身就是对话循环,保留其中"工具、提示词、上下文构建"的行为 | 待读 |
| `agents/question`、`capabilities/ask_questions` | 出题与提问能力 | **复刻**(仿写已做) | 部分已做 |
| `agents/vision_solver`、`capabilities/solve` | 解题(含图片) | **复刻**;图片理解依赖 Claude 的多模态,可行 | 待读 |
| `agents/research`、`services/research`、`capabilities/explore_context` | 深度研究 | **复刻**;检索外部资料需要联网工具,是否允许待你定 | 待读 |
| `agents/visualize`、`visualizers` | 图示与可视化 | **复刻**(Mermaid/SVG 由 Claude 生成,不需要其他模型) | 待读 |
| `agents/notebook`、`services/notebook`、`co_writer` | 笔记本与协作写作 | **复刻** | 待读 |
| `reading`、`book`、`textbook_struct`、`capabilities/reading` | 沉浸式阅读、书籍、教材结构 | **复刻**,与现有知识库对接 | 待读 |
| `knowledge`、`services/rag`、`services/parsing` | 知识库、检索、解析 | **部分复刻**:建库/解析/搜索/评估/同步来源可做;**向量/图检索不做**(需要嵌入模型) | 待读 |
| `capabilities/course_study`、`services/courses*` | 课程学习 | **复刻** | 待读 |
| `capabilities/mastery`(其余) | 掌握路径能力 | **复刻**,与已有路径对齐 | 待读 |
| `services/task_board.py` | 任务看板 | **换形态**:文件加命令 | 待读 |
| `services/skill`、`skills`、`plugins`、`services/mcp`、`services/cli_apps` | 技能/插件/MCP 生态 | **不做**:Claude Code 自带,不重复造 | 待核实 |
| `agents/math_animator`、`video_learning`、`services/imagegen`、`videogen`、`voice`、`capabilities/audio_overview` | 数学动画、视频学习、生成与语音 | **暂不做**(需要其他模型/渲染链) | 已定 |
| `multi_user`、`services/auth`、`api`、`app`、`runtime`、`partners`、`capabilities/partner_*`、`services/cron`、`services/sandbox`、`services/setup` | 多用户、鉴权、Web 服务、IM 伙伴、定时任务、沙箱、安装向导 | **不做**:没有常驻服务 | 已定 |
| `capabilities/ima`、`marginnote4`、`obsidian` 与对应 `services` | 第三方知识源接入 | **待定**:Obsidian 这类本地文件源可能可做 | 待读 |

## 3. 版本顺序提案

规则不变:次版本加功能,修订版只修缺陷;**每个次版本开工前先读对应上游源码并写行为清单,你确认后再写代码**。

| 版本 | 内容 | 验收 |
|---|---|---|
| **0.5.0** | 记忆系统:L1 追加与读取、快照(`chat`/`quiz`/`kb` 三个 surface)、L2/L3 文档读写、偏好写入、概览、整合(update/audit/dedup/merge)、近期回顾 | 清单 M1–M45;纯函数部分对照上游 `tests/services/memory/` 做黄金向量;整合在真实会话里逐条跑;变异检查 |
| 0.6.0 | `learning/` 剩余模块、`learning_journal`、`persona`、学习者画像(含入门画像)、`practice` | 每个模块一份行为清单 |
| 0.7.0 | 问答与能力:`ask_questions`、`solve`、`visualize`、`research`、`explore_context` | 同上 |
| 0.8.0 | 阅读与资料:`reading`/`book`/`textbook_struct`、`knowledge` 的可做部分、`notebook`、`co_writer`、任务看板 | 同上 |
| 0.9.0 | `course_study`、`mastery` 其余、第三方知识源(按可行性) | 同上 |
| **1.0.0** | 全部清单验收完成;数据格式固定并带迁移;文档完整;公开发布 | 清单全部打勾,有 CI |

原路线图的 0.5.0(可视化)和 0.6.0(论文检索与研究)被吸收进 0.7.0。"暂不规划"清单收窄为"已定不做"(见第 2 节)。

## 4. 需要改的项目文件(你确认后再动)

- `CLAUDE.md`:把第一条原则"目标是更深入,不是更大:先把建库、讲解、出题、复习四项核心做扎实,不扩展新功能"改为"目标是逐模块 1:1 复刻 DeepTutor,只把推理换成 Claude Code;每个模块先读上游源码、写行为清单、经确认再实现;有意偏离要在清单里标注"。其余开发流程、模块化约定、经验教训保持不变。
- `docs/DESIGN.md` 第 6 节:路线图按第 3 节重排,"暂不规划"改为"已定不做"并写明理由。
- 新增 `docs/upstream-behavior/`:每个模块一份清单,格式同 `memory.md`。

## 5. 风险与未决问题

1. **向量/图检索永远不会 1:1。** 知识库是原项目的核心之一,我们的页面级全文检索只能算功能对等的替代。这一条要在 `knowledge` 的行为清单里明写为"有意偏离"。
2. **体量。** 上游 `deeptutor/learning/` 约 1.3 万行,整个后端约 31 万行(上一轮统计)。逐文件行为清单本身就是很大的工作量,0.5.0 之后每个版本都会更大。要不要给每个版本设一个"读多少源码"的上限?
3. **"自动"类行为。** 凡是上游靠后台进程自动做的事(记忆整合、同步来源、定时任务),在插件里都变成显式命令或技能收尾提示。这些是系统性的有意偏离,建议在 `DESIGN.md` 里单列一节,而不是每个清单里重复解释。
4. **联网能力。** 深度研究和同步网页/GitHub 来源需要联网;是否允许插件联网,或者只用 Claude Code 自带的联网工具,需要你定。
5. **已有功能与上游的关系需要回头核对。** 0.1–0.4 我是凭 DeepTutor 的文档和部分源码做的,并没有逐文件行为清单。1:1 的标准下,这几个版本也要补清单(至少 `mastery`、`scheduler`、`policy`、`teaching`、`mimic_source` 五处)。

## 6. 待你确认

1. 第 2 节的处置提案(尤其是"不做"和"待定"两类)可以吗?
2. 第 3 节的版本顺序可以吗?
3. 第 5 节第 2、4、5 点怎么处理?
4. 确认后我再改 `CLAUDE.md` 和 `docs/DESIGN.md`。
