# deep-tutor-with-claude-code — 结构与流程设计

状态:已确认,0.1.0 至 0.4.0 的功能已实现(0.4.0 尚未发布)。**2026-11-22 起项目目标改为逐模块 1:1 复刻 DeepTutor、只换推理,第 6 节路线图已按此重排;第 0 节记录的是最初"做深不做大"的思路,作为历史保留。****第 1 节的目录结构是最初设计,与实际实现有出入,以 `docs/verification/0.1.0.md` 的偏差清单和仓库实际结构为准**(技能名为 `kb`/`learn`/`path`/`quiz`/`review`;三个知识库脚本合并为 `kb.py`;页面只存 SQLite;无 pyproject)。依据:`DeepTutor-main/` 源码(下称 DT),以下引用均指向该目录内的具体文件。

## 0. 先答四个问题(决定"更深"而不是"更大")——历史记录,目标已于 2026-11-22 改变,见第 6 节

**1. 顶尖专家会做、而题目没要求的事**
- 每条讲解都能追溯到资料原文(文件 + 页/幻灯片),引用的原句经脚本逐字核对,查不到就不许引。
- 把"掌握度、遗忘、间隔复习"做成可复算的确定性代码,而不是让模型凭印象说"你掌握了"。DT 自己也这样做:门槛由引擎判定,"not by your impression"(`capabilities/mastery/prompts/en/mastery_loop.yaml`)。
- 错题要分类、能追根,并在之后重测,而不是只存一个"错题本"。
- 声明已知局限:DT 的调度器注明"不是校准过的 FSRS"(`learning/scheduler.py` 的 `RetentionScheduler` 文档),我们同样如实标注。

**2. 从最原始的素材入手**
- 不看 README 的功能介绍,直接读 DT 的算法源码:`learning/mastery.py`、`scheduler.py`、`grading.py`、`models.py`、`teaching.py`、`learning/prompts/en.yaml`。
- 学习资料侧:用户原始 PDF/PPT/讲义,不是我整理的摘要。
- 社区项目 ndpvt-web 只作旁证,不当依据。

**3. 具体方法与工具**
- 知识库:`pdftotext -layout` 按页切分(本机已有 `/opt/homebrew/bin/pdftotext`),文本存 Markdown,索引用 Python 标准库 `sqlite3` 的 FTS5(本机已验证可用)。不用嵌入模型,不调用任何其他模型 API;查询改写和重排由 Claude 做。
- 教学环:沿用 DT 的阶段 诊断 → 讲解 → 费曼检查 → 练习 → 错因诊断 → 复习(`learning/models.py` 的 `LearningStage`)。
- 掌握度:移植 `compute_mastery`(近 5 次加权 + 低证据封顶)。
- 复习:移植指数遗忘 + 稳定度更新(`SpacedRepetitionScheduler`)。
- 错因四类:structural / deviation / application / metacognitive(`ErrorType`)。

**4. 除你我之外的验证依据**
- 黄金向量测试:用 DT 原代码对一组固定输入跑出结果,存成 JSON;我们的移植版必须逐项一致。这是对照第三方源码的外部证据。
- 引用核对脚本:讲解/出题里的原文引用必须能在知识库文本中逐字找到。
- 许可证核对:保留 Apache-2.0 全文;`pyproject.toml` 标注 `Apache-2.0`;在 NOTICE 中写明移植自 HKUDS/DeepTutor 的文件与修改。
- 用 1 份真实讲义做端到端演练,留下可复现记录。

## 1. 插件结构(Claude Code plugin)

```
deep-tutor-with-claude-code/
├── .claude-plugin/plugin.json
├── LICENSE                      # Apache-2.0 原文(取自 DT)
├── NOTICE                       # 出处、版权归属、修改说明
├── skills/
│   ├── tutor-kb/SKILL.md        # 建库 / 增量添加 / 检索
│   ├── tutor-learn/SKILL.md     # 基于资料的讲解 + 费曼检查 + 问答
│   ├── tutor-quiz/SKILL.md      # 出题、判分、错题记录与错因诊断
│   └── tutor-review/SKILL.md    # 按遗忘风险排复习队列
├── scripts/                     # 仅 Python 标准库(pdftotext 为可选外部命令)
│   ├── kb_ingest.py             # 切页 → md → FTS5 索引
│   ├── kb_search.py             # 检索并返回带页码的片段
│   ├── verify_quotes.py         # 引用逐字核对
│   ├── mastery.py               # 移植 compute_mastery
│   ├── scheduler.py             # 移植调度器
│   └── ledger.py                # 记录作答/错题/复习状态(JSON)
├── tests/
│   ├── golden/                  # 由 DT 原代码生成的黄金向量
│   └── test_*.py
└── docs/DESIGN.md
```

用户数据放在使用者自己的项目里:`.tutor/kb/<name>/`(文本与索引)、`.tutor/learner/<name>.json`(作答、错题、复习状态)。

## 2. 流程

**建库** `tutor-kb`:用户给文件/目录 → ingest 切页、抽文本、建索引 → 汇报每个文件的页数与抽取失败项(扫描版 PDF 无文字层要明说,不假装读过)。

**讲解问答** `tutor-learn`:问题 → Claude 改写查询 → `kb_search` 取片段 → 先类比、再精确定义、给例子、点出常见误解(取自 DT `explain` 提示)→ 附出处 → `verify_quotes` 通过后才输出 → 可选费曼检查:让用户复述,Claude 按"简单语言/遗漏/误解"评判并指出缺口。资料里没有的内容要标明"资料未覆盖,以下为通识"。

**出题自测** `tutor-quiz`:按知识点出题(选择/填空/简答,每题带答案、解析、来源页)→ 选择题由脚本确定性判分,简答由 Claude 按要点评分并给出理由 → `ledger` 记录 → 答错的归入四类错因,隔一段时间重测,连续答对才"毕业"。

**复习** `tutor-review`:`scheduler` 计算每个知识点的可提取率与遗忘风险,排出到期队列(风险高、逾期久、有错题者优先)→ 出复习题 → 更新状态。

## 3. 0.1.0 暂不做(推迟,见第 6 节路线图)

- 不做 Web 界面、IM 伙伴、视频/动画、网络检索、嵌入向量检索。
- 不做 DT 的"掌握路径大纲 + 硬门槛"整套(`capabilities/mastery`),只借其掌握度与阶段思想。理由:先把四项核心做深。
- DT 的简答判分是模糊字符串匹配(`learning/grading.py`),过于粗糙,我们不移植,改由 Claude 评分、脚本只做记录。

## 4. 已确认的决定

1. **数据位置**:各项目内的隐藏文件夹 `.tutor/`。(用户已确认是 `.tutor/`。)
2. **语言**:讲解、出题、反馈的语言跟随用户当前使用的语言(2026-10-24 改;原定默认英文,已取消);引文保持原文语言,不翻译。
3. **扫描版 PDF**:由 Claude 自动处理,用户不需要自己做 OCR 或转换。方案见 4.1。
4. **范围**:第 3 节的"不做"项不是永久删除,而是推迟到后续版本。版本规则与路线图见第 6 节。

### 4.1 资料导入方案(用户不用动手)

按文件类型自动分流,全部写入同一份带页码的 Markdown:

| 类型 | 做法 |
|---|---|
| 有文字层的 PDF | `pdftotext -layout`,以换页符切页,保证每段有页码 |
| pptx / docx / xlsx | 调用本机已有的 `markitdown`;幻灯片保留序号(实现时核实输出格式) |
| md / txt | 直接导入 |
| 扫描版 PDF(或某些页无文字层) | 逐页判断:文字少于阈值的页用 `pdftoppm` 渲染成图片,由 Claude 看图转写成 Markdown,**转写结果缓存进知识库,只做一次** |

要点:
- 不用 markitdown 处理 PDF,因为它不保证保留页码,而引用核对依赖页码。PDF 页码分割靠 `pdftotext`。
- 转写缓存之后检索全走索引,不再花额度。额度只在首次转写时按页消耗,所以大本扫描教材导入前会先告知页数,让用户确认再开始。
- 转写页在索引中标记为"AI 转写",引用核对时对这类页给出更低的置信度提示,因为转写本身可能有错。
- 本机已确认有 `markitdown`(命令行)和 `pdftoppm`;`markitdown` 不是 Python 模块,脚本需通过命令行调用。插件在首次使用时检查这些工具,缺少就告诉用户装什么。

## 6. 版本规则与路线图

规则(采用语义化版本的思路):
- **0.X.0(次版本)**:新增功能。
- **0.X.Y(修订版)**:缺陷修复、维护、性能优化,不加新功能。
- **1.0.0**:不是"功能更多",而是稳定承诺:数据格式固定并带迁移、有文档、测试覆盖四项核心、可作为公开插件发布。

修订版(0.X.Y)不预先写具体内容,因为缺陷事先未知;只预先划定类别。路线图只规划次版本。

| 版本 | 做什么 | 来源(DeepTutor 对应处) |
|---|---|---|
| **0.1.0** | 四项核心:建库(第 4.1 节导入)、带出处的讲解问答、出题与判分与错题记录、按遗忘风险复习;掌握度与调度器移植;黄金向量测试;NOTICE 与许可证 | `learning/mastery.py`、`scheduler.py`、`models.py` |
| 0.1.x | 缺陷修复、导入速度与检索排序优化、提示词措辞调整;**不加功能** | — |
| **0.2.0** (已实现) | 学习路径:按目标生成模块与知识点大纲、前置依赖、诊断测验、费曼检查升级为正式阶段、硬性掌握门槛 | `capabilities/mastery/`、`learning/topic_generation.py` |
| **0.3.0** (已实现) | 真题仿写出题、题库分类归档 | `agents/question/mimic_source.py` |
| **0.4.0** | 学习者画像与教学反馈:记录哪些讲解方式有效、哪些无效,并据此换讲法(必须引用学习者原话) | `learning/teaching.py` |
| **0.5.0** | 记忆系统数据层:L1 追加与读取、快照(`chat`/`quiz`/`kb`)、L2/L3 文档读写、偏好写入、概览、近期回顾;`/tutor:memory`;行为清单见 `docs/upstream-behavior/memory.md` | `services/memory/`(ids、document、ops、trace、store、recall、snapshot) |
| 0.6.0 | 记忆整合(update/audit/dedup/merge)、读取账本与教学档案做整合输入 | `services/memory/`(consolidator、modes、chunker、references、runs) |
| 0.7.0 | `learning/` 剩余模块、学习日志、人设、学习者画像(含入门画像)、练习 | `learning/`、`services/learning_journal`、`services/persona`、`services/practice` |
| 0.8.0 | 问答与能力:提问、解题、可视化、深度研究、上下文探索 | `capabilities/ask_questions`、`solve`、`agents/visualize`、`agents/research`、`capabilities/explore_context` |
| 0.9.0 | 阅读与资料:阅读/书籍/教材结构、知识库可做的部分、笔记本、协作写作、任务看板 | `reading`、`book`、`textbook_struct`、`knowledge`、`agents/notebook`、`co_writer` |
| 0.10.0 | 课程学习、掌握路径其余部分、第三方知识源(按可行性) | `capabilities/course_study`、`capabilities/mastery`、`capabilities/obsidian` 等 |
| **1.0.0** | 稳定版:全部行为清单验收完成,数据格式固定并带迁移,文档完整,有 CI,公开发布 | — |

**已定不做**(2026-11-22 确认):
- 需要其他模型的功能:向量/图检索(知识库只做页面级全文检索,算有意偏离)、图像/视频/语音生成、TTS/STT、数学动画、视频学习。
- 没有常驻服务的形态下无法成立的功能:Web 界面、多用户与鉴权、IM 伙伴与合作伙伴、定时任务、沙箱、安装向导。
- Claude Code 自带的能力:技能/插件/MCP 生态。

**系统性的有意偏离**:上游靠后台进程自动做的事(记忆整合、来源同步等),在插件里改成用户显式命令或技能收尾提示。

**未决**(路线图草案第 5 节,尚未拍板):每个版本"读多少上游源码"的上限;深度研究与同步网页/GitHub 来源是否允许联网;0.1–0.4 补写逐文件行为清单(至少 `mastery`、`scheduler`、`policy`、`teaching`、`mimic_source` 五处)的时机。

每个次版本开工前,先读对应的 DeepTutor 源码,写行为清单,你确认后再写代码。完整盘点与处置见 `docs/ROADMAP-1to1-DRAFT.md`。

(历史,0.1.0 当时的范围)0.1.0 的范围坚持"做深不做大":扫描版转写和 pptx/docx 导入算在"建库"之内,因为课件与讲义是核心场景;不放进 0.1.0 的是路径、仿写、画像、可视化、研究。

## 7. 未核实项

- DT 源码里没有逐文件的版权头(`grep Copyright` 无命中),版权方只能依据仓库 `LICENSE`、`pyproject.toml`、README 标注;NOTICE 中的版权人写法待你确认。
- 尚未读 `learning/assessment.py`、`policy.py`、`question_card.py` 的细节;实现对应模块前会先读。
