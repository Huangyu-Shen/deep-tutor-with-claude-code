# 模块职责

> 模块划分依据：仓库既有边界——`skills/` 下的每个技能、`scripts/` 下的每个脚本都是独立模块（[CLAUDE.md:25-28](../../CLAUDE.md#L25-L28)）。下文按“运行时重要性”排序。

## 目录

- 脚本：[知识库](#知识库脚本-scriptskbpy) · [学习路径](#学习路径脚本-scriptspathpy) · [账本](#账本脚本-scriptsledgerpy) · [事件契约](#事件契约库-scriptslearner_logpy) · [调度器](#调度器库-scriptsschedulerpy) · [掌握度](#掌握度库-scriptsmasterypy)
- 技能：[kb](#技能-tutorkb) · [learn](#技能-tutorlearn) · [quiz](#技能-tutorquiz) · [review](#技能-tutorreview) · [path](#技能-tutorpath)
- 支撑：[测试](#测试套件-tests) · [打包](#插件打包-claude-plugin) · [文档](#项目文档-docs) · [非运行时目录](#非运行时目录)

## 模块总览

| 模块 | 路径 | 职责 | 关键程度 |
| --- | --- | --- | --- |
| 知识库脚本 | [`scripts/kb.py`](../../scripts/kb.py) | 抽取、切页、索引、检索、引文核对 | 核心 |
| 学习路径脚本 | [`scripts/path.py`](../../scripts/path.py) | 路径定义校验、门槛、状态重放、可学范围判定 | 核心 |
| 账本脚本 | [`scripts/ledger.py`](../../scripts/ledger.py) | 作答/错题记录、状态与复习队列导出 | 核心 |
| 事件契约库 | [`scripts/learner_log.py`](../../scripts/learner_log.py) | 事件校验、日志读取、追加写入 | 核心 |
| 调度器库 | [`scripts/scheduler.py`](../../scripts/scheduler.py) | 指数遗忘排期、可提取率、遗忘风险 | 核心 |
| 掌握度库 | [`scripts/mastery.py`](../../scripts/mastery.py) | 近因加权掌握度与低证据封顶 | 核心 |
| 技能 `/tutor:kb` | [`skills/kb/SKILL.md`](../../skills/kb/SKILL.md) | 建库与扫描页转写的编排 | 核心 |
| 技能 `/tutor:learn` | [`skills/learn/SKILL.md`](../../skills/learn/SKILL.md) | 讲解、引用核对、费曼检查的编排 | 核心 |
| 技能 `/tutor:quiz` | [`skills/quiz/SKILL.md`](../../skills/quiz/SKILL.md) | 出题、判分、错因诊断的编排 | 核心 |
| 技能 `/tutor:review` | [`skills/review/SKILL.md`](../../skills/review/SKILL.md) | 复习队列与复习会话的编排 | 支撑 |
| 技能 `/tutor:path` | [`skills/path/SKILL.md`](../../skills/path/SKILL.md) | 路径生成/确认、诊断、豁免的编排 | 支撑 |
| 测试套件 | [`tests/`](../../tests) | 单元、黄金向量、变异检查、端到端演练 | 支撑 |
| 插件打包 | [`.claude-plugin/`](../../.claude-plugin) | 插件与市场清单 | 基础设施 |
| 项目文档 | [`docs/`](../) | 设计、验证、经验沉淀 | 辅助 |

## 知识库脚本 `scripts/kb.py`

- **路径**：[`scripts/kb.py`](../../scripts/kb.py)，910 行
- **职责**：把 PDF/pptx/docx/xlsx/md/txt 切成带页码/幻灯片号的文本单元，写入 SQLite FTS5；提供检索、取页、渲染、登记转写、引文核对、移除与列表。不负责判断“该不该教”。
- **对外接口**（CLI，[scripts/kb.py:9-18](../../scripts/kb.py#L9-L18)、[scripts/kb.py:841-875](../../scripts/kb.py#L841-L875)）：
  - `ingest PATH... [--scanned]`
  - `status`、`list`
  - `search QUERY [--query Q] [--mode any|all] [--limit N] [--no-text]`
  - `get --source S --page N`
  - `render --source S --pages 1,3-5`
  - `register --source S --page N`（正文从 stdin 读）
  - `verify`（JSON 从 stdin 读）
  - `remove --source S`
- **关键文件**：单文件模块；数据库 schema 在 [scripts/kb.py:57-73](../../scripts/kb.py#L57-L73)，抽取分派在 [scripts/kb.py:247-257](../../scripts/kb.py#L247-L257)，检索在 [scripts/kb.py:517-579](../../scripts/kb.py#L517-L579)，核对在 [scripts/kb.py:776-835](../../scripts/kb.py#L776-L835)。
- **内部依赖**：无（不导入其他脚本）。
- **外部依赖**：Python 标准库 `sqlite3`、`hashlib`、`subprocess`；外部命令 `pdftotext`、`pdftoppm`、`pdfimages`、`markitdown`。
- **边界与注意**：只接受字母/数字/`_`/`.`/`-` 开头的知识库名（[scripts/kb.py:86-89](../../scripts/kb.py#L86-L89)）；单文件一个事务，坏文件不会回滚好文件（[scripts/kb.py:396-405](../../scripts/kb.py#L396-L405)）；扫描判定是文档级比例而非单页（[scripts/kb.py:39-42](../../scripts/kb.py#L39-L42)）。

## 学习路径脚本 `scripts/path.py`

- **路径**：[`scripts/path.py`](../../scripts/path.py)，1,071 行
- **职责**：校验/保存/切换/删除路径定义；从路径定义 + 事件日志重放模块状态；计算每个知识点的门槛缺口；回答“现在能不能学/考这个模块或知识点”。
- **对外接口**（CLI，[scripts/path.py:13-25](../../scripts/path.py#L13-L25)、[scripts/path.py:1003-1046](../../scripts/path.py#L1003-L1046)）：`list/show/validate/save/activate/deactivate/rename/delete/status/resolve`。
- **关键文件**：单文件模块；结构校验 [scripts/path.py:150-220](../../scripts/path.py#L150-L220)，证据校验 [scripts/path.py:294-337](../../scripts/path.py#L294-L337)，门槛 [scripts/path.py:498-536](../../scripts/path.py#L498-L536)，状态重放 [scripts/path.py:590-613](../../scripts/path.py#L590-L613)，状态载荷 [scripts/path.py:632-722](../../scripts/path.py#L632-L722)，可学判定 [scripts/path.py:943-987](../../scripts/path.py#L943-L987)。
- **内部依赖**：`learner_log.py`（事件读取与 id）、`mastery.py`（展示掌握度）。
- **外部依赖**：标准库 `sqlite3`（只读证据页）、`json`、`argparse`。
- **边界与注意**：路径文件只存配置，模块状态不写回（[scripts/path.py:3-6](../../scripts/path.py#L3-L6)）；`diagnostic` 永不开门，`exemption` 可开门（[scripts/path.py:51](../../scripts/path.py#L51)）；证据页消失会阻断门槛并向下游传播 `invalidated_by`（[scripts/path.py:660-666](../../scripts/path.py#L660-L666)）。

## 账本脚本 `scripts/ledger.py`

- **路径**：[`scripts/ledger.py`](../../scripts/ledger.py)，307 行
- **职责**：追加学习事件；从事件日志重放知识点、错题记录与复习状态；输出状态、到期队列、错题与导出。
- **对外接口**（CLI，[scripts/ledger.py:21-27](../../scripts/ledger.py#L21-L27)、[scripts/ledger.py:266-284](../../scripts/ledger.py#L266-L284)）：`record`（stdin JSON）、`now`、`state`、`due [--limit] [--kb]`、`errors [--status]`、`export [--format json|md]`。
- **关键文件**：单文件模块；重放 [scripts/ledger.py:63-113](../../scripts/ledger.py#L63-L113)，排期 [scripts/ledger.py:116-137](../../scripts/ledger.py#L116-L137)，状态 [scripts/ledger.py:149-175](../../scripts/ledger.py#L149-L175)，到期队列 [scripts/ledger.py:178-196](../../scripts/ledger.py#L178-L196)。
- **内部依赖**：`learner_log.py`、`mastery.py`、`scheduler.py`。
- **外部依赖**：标准库 `json`、`argparse`、`datetime`。
- **边界与注意**：写入前先用“现有事件 + 新事件”跑一遍重放，坏事件不会落盘（[scripts/ledger.py:232-241](../../scripts/ledger.py#L232-L241)）；错题连续两次答对才毕业（[scripts/ledger.py:57](../../scripts/ledger.py#L57)）。

## 事件契约库 `scripts/learner_log.py`

- **路径**：[`scripts/learner_log.py`](../../scripts/learner_log.py)，241 行
- **职责**：定义唯一的日志契约（事件类型、字段、枚举），提供 `kp_id` 规范化、严格 JSON 读取、逐行容错、事件校验与持久追加。它是纯库，没有 CLI。
- **对外接口**（Python 函数）：`kp_id`、`read_events`、`validate_event`、`prepare_event`、`mastery_scores`、`append_line`、`log_path`。
- **关键文件**：枚举与常量 [scripts/learner_log.py:29-37](../../scripts/learner_log.py#L29-L37)，日志路径 [scripts/learner_log.py:69-72](../../scripts/learner_log.py#L69-L72)，读取容错 [scripts/learner_log.py:84-119](../../scripts/learner_log.py#L84-L119)，校验 [scripts/learner_log.py:139-184](../../scripts/learner_log.py#L139-L184)，补默认值 [scripts/learner_log.py:187-215](../../scripts/learner_log.py#L187-L215)。
- **内部依赖**：`scheduler.py`（复用知识类型常量）。
- **外部依赖**：标准库 `json`、`re`、`unicodedata`、`uuid`、`os`。
- **边界与注意**：不导入 `ledger.py` 或 `path.py`（[scripts/learner_log.py:4-5](../../scripts/learner_log.py#L4-L5)）；读取时容忍未知 `origin`（计入掌握度、不开门、给警告），写入时拒绝（[scripts/learner_log.py:113-117](../../scripts/learner_log.py#L113-L117)、[scripts/learner_log.py:201-203](../../scripts/learner_log.py#L201-L203)）。

## 调度器库 `scripts/scheduler.py`

- **路径**：[`scripts/scheduler.py`](../../scripts/scheduler.py)，449 行
- **职责**：把作答序列回放成复习状态；计算可提取率、遗忘风险、优先级与复习队列。数值逻辑移植自 DeepTutor 的指数遗忘基线。
- **对外接口**（Python 类/函数）：`LearningEvidence`、`RepetitionState`、`ReviewTask`、`SpacedRepetitionScheduler`（`replay`、`schedule_review`、`retrievability`、`forgetting_risk`、`build_review_queue`）、`review_sort_key`。
- **关键文件**：类型表 [scripts/scheduler.py:26-34](../../scripts/scheduler.py#L26-L34)，状态更新 [scripts/scheduler.py:200-287](../../scripts/scheduler.py#L200-L287)，可提取率 [scripts/scheduler.py:289-294](../../scripts/scheduler.py#L289-L294)，遗忘风险 [scripts/scheduler.py:296-314](../../scripts/scheduler.py#L296-L314)，回放 [scripts/scheduler.py:391-419](../../scripts/scheduler.py#L391-L419)。
- **内部依赖**：无。
- **外部依赖**：标准库 `math`、`dataclasses`。
- **边界与注意**：文档明确声明“不是校准过的 FSRS”（[scripts/scheduler.py:13](../../scripts/scheduler.py#L13)）；删除了上游的 `LEARNING_DEBUG` 秒级调试开关，避免环境变量意外改变排期（[scripts/scheduler.py:114-117](../../scripts/scheduler.py#L114-L117)）。

## 掌握度库 `scripts/mastery.py`

- **路径**：[`scripts/mastery.py`](../../scripts/mastery.py)，51 行
- **职责**：`compute_mastery(correctness) -> float`，近 5 次作答按 `0.5/0.7/0.85/0.95/1.0` 加权，并按证据数量封顶。
- **对外接口**：`compute_mastery`；附带一个 stdin/stdout JSON CLI。
- **关键文件**：权重与封顶 [scripts/mastery.py:20-24](../../scripts/mastery.py#L20-L24)，主函数 [scripts/mastery.py:27-36](../../scripts/mastery.py#L27-L36)。
- **内部依赖**：无。
- **外部依赖**：标准库 `json`、`sys`。
- **边界与注意**：只接收无提示作答的分数（由调用方过滤，[scripts/learner_log.py:225-227](../../scripts/learner_log.py#L225-L227)）；不判断门槛，门槛在 `path.py`。

## 技能 `/tutor:kb`

- **路径**：[`skills/kb/SKILL.md`](../../skills/kb/SKILL.md)，75 行
- **职责**：选择知识库名、导入文件、处理扫描页（渲染 → Claude 读图 → 登记转写）、汇报读取与未读取内容、删除资料。
- **对外接口**：参数提示 `[kb name] [files or folders...]`；允许工具仅 `Edit(**/.tutor/tmp/*)`、`Bash(python3 *scripts/kb.py*)`、`Read`（[skills/kb/SKILL.md:1-6](../../skills/kb/SKILL.md#L1-L6)）。
- **关键文件**：建库步骤 [skills/kb/SKILL.md:20-40](../../skills/kb/SKILL.md#L20-L40)，扫描页 [skills/kb/SKILL.md:41-65](../../skills/kb/SKILL.md#L41-L65)，规则 [skills/kb/SKILL.md:71-75](../../skills/kb/SKILL.md#L71-L75)。
- **内部依赖**：`scripts/kb.py`。
- **边界与注意**：一次 Bash 调用只跑一条脚本命令；JSON 先写入 `.tutor/tmp/` 新文件再重定向（[skills/kb/SKILL.md:14-18](../../skills/kb/SKILL.md#L14-L18)）。

## 技能 `/tutor:learn`

- **路径**：[`skills/learn/SKILL.md`](../../skills/learn/SKILL.md)，143 行
- **职责**：检查活动路径 → 改写查询 → 检索 → 讲解 → 引用核对 → 标注资料未覆盖 → 可选费曼检查。
- **对外接口**：参数提示 `[kb name] <question or topic>`；允许 `kb.py`、`ledger.py`、`path.py`、临时写、读（[skills/learn/SKILL.md:1-6](../../skills/learn/SKILL.md#L1-L6)）。
- **关键文件**：活动路径 [skills/learn/SKILL.md:16-39](../../skills/learn/SKILL.md#L16-L39)，找资料 [skills/learn/SKILL.md:46-57](../../skills/learn/SKILL.md#L46-L57)，讲解 [skills/learn/SKILL.md:58-67](../../skills/learn/SKILL.md#L58-L67)，核对 [skills/learn/SKILL.md:68-92](../../skills/learn/SKILL.md#L68-L92)，费曼 [skills/learn/SKILL.md:97-138](../../skills/learn/SKILL.md#L97-L138)。
- **内部依赖**：`kb.py`、`ledger.py`、`path.py`。
- **边界与注意**：不自己判断模块能否学习，必须调用 `path.py resolve`；引文不过核对不得展示。

## 技能 `/tutor:quiz`

- **路径**：[`skills/quiz/SKILL.md`](../../skills/quiz/SKILL.md)，123 行
- **职责**：按路径/知识点出题（选择、填空、简答），判分，记录结果，归入四类错因，安排重测并关闭会话。
- **对外接口**：参数提示 `[kb name] [topic] [number of questions]`；允许脚本、临时写、读、`Edit(**/.tutor/quiz/*)`（[skills/quiz/SKILL.md:1-6](../../skills/quiz/SKILL.md#L1-L6)）。
- **关键文件**：活动路径 [skills/quiz/SKILL.md:14-31](../../skills/quiz/SKILL.md#L14-L31)，出题 [skills/quiz/SKILL.md:38-72](../../skills/quiz/SKILL.md#L38-L72)，判分 [skills/quiz/SKILL.md:77-100](../../skills/quiz/SKILL.md#L77-L100)，错因 [skills/quiz/SKILL.md:101-118](../../skills/quiz/SKILL.md#L101-L118)。
- **内部依赖**：`kb.py`、`ledger.py`、`path.py`。
- **边界与注意**：判分由 Claude 按答案要点/清单完成；账本只记录，不判分（[README.md:71](../../README.md#L71)）。

## 技能 `/tutor:review`

- **路径**：[`skills/review/SKILL.md`](../../skills/review/SKILL.md)，69 行
- **职责**：从 `ledger.py due` 取到期队列，逐项复习，记录结果并收尾。
- **对外接口**：参数提示 `[kb name] [how many items]`；允许 `kb.py`、`ledger.py`、临时写、读（[skills/review/SKILL.md:1-6](../../skills/review/SKILL.md#L1-L6)）。
- **关键文件**：取队列 [skills/review/SKILL.md:20-31](../../skills/review/SKILL.md#L20-L31)，逐项复习 [skills/review/SKILL.md:32-60](../../skills/review/SKILL.md#L32-L60)，规则 [skills/review/SKILL.md:65-69](../../skills/review/SKILL.md#L65-L69)。
- **内部依赖**：`ledger.py`、`kb.py`。
- **边界与注意**：复习不受模块锁限制（[CHANGELOG.md:10](../../CHANGELOG.md#L10)）；调度器是估计基线，措辞必须说“estimated”。

## 技能 `/tutor:path`

- **路径**：[`skills/path/SKILL.md`](../../skills/path/SKILL.md)，193 行
- **职责**：从目标生成模块/知识点大纲并逐模块确认；切换/重命名/停用/删除路径；运行起始诊断；执行模块豁免。
- **对外接口**：参数提示 `[kb name] [action]`；允许 `path.py`、`kb.py`、`ledger.py`、临时写、读（[skills/path/SKILL.md:1-6](../../skills/path/SKILL.md#L1-L6)）。
- **关键文件**：创建路径 [skills/path/SKILL.md:58-104](../../skills/path/SKILL.md#L58-L104)，切换/删除 [skills/path/SKILL.md:105-130](../../skills/path/SKILL.md#L105-L130)，诊断 [skills/path/SKILL.md:131-165](../../skills/path/SKILL.md#L131-L165)，豁免 [skills/path/SKILL.md:166-185](../../skills/path/SKILL.md#L166-L185)。
- **内部依赖**：`path.py`、`kb.py`、`ledger.py`。
- **边界与注意**：不教日常内容、不跑普通测验；模块状态只渲染脚本输出（[skills/path/SKILL.md:10-12](../../skills/path/SKILL.md#L10-L12)）。

## 测试套件 `tests/`

- **路径**：[`tests/`](../../tests)，4 个测试文件共 2,142 行、166 个用例
- **职责**：
  - [`tests/test_kb.py`](../../tests/test_kb.py)：抽取、扫描判定、检索模式、引文核对状态、转写缓存、异常输入（67 个用例）。
  - [`tests/test_path.py`](../../tests/test_path.py)：路径校验、门槛、状态重放、诊断/豁免、失效证据、CLI 契约（57 个用例）。
  - [`tests/test_ledger.py`](../../tests/test_ledger.py)：事件校验、重放、错题毕业、导出（38 个用例）。
  - [`tests/test_golden.py`](../../tests/test_golden.py)：与 DeepTutor 原实现产出的黄金向量逐项比对（4 个用例）。
- **关键文件**：黄金向量 [tests/golden/vectors.json](../../tests/golden/vectors.json)，生成器 [tests/golden/generate.py](../../tests/golden/generate.py)（需要 DeepTutor checkout），变异检查 [tests/golden/mutation_check.py](../../tests/golden/mutation_check.py)，端到端演练 [tests/walkthrough.sh](../../tests/walkthrough.sh)。
- **内部依赖**：全部脚本。
- **外部依赖**：标准库 `unittest`；黄金向量生成额外需要 `DeepTutor-main/` 的 `deeptutor` 包。
- **边界与注意**：测试使用临时目录，不写用户 `.tutor/`；166 个用例在本机约 10 秒完成。

## 插件打包 `.claude-plugin/`

- **路径**：[`.claude-plugin/`](../../.claude-plugin)
- **职责**：声明插件元数据与 marketplace，使仓库可被 `claude plugin install` 安装。
- **关键文件**：[plugin.json](../../.claude-plugin/plugin.json)、[marketplace.json](../../.claude-plugin/marketplace.json)。
- **边界与注意**：`source` 指向仓库根，因此安装后技能、脚本、NOTICE 一起分发（[marketplace.json:4-14](../../.claude-plugin/marketplace.json#L4-L14)）。

## 项目文档 `docs/`

- **路径**：[`docs/`](../)
- **职责**：设计与范围（`DESIGN.md`）、版本验证记录（`verification/`）、可复用经验（`solutions/`）、计划与头脑风暴（`plans/`、`brainstorms/`）、早期复盘（`compound/`）。
- **边界与注意**：新的可复用经验统一进 `docs/solutions/<分类>/`，每篇带 YAML frontmatter（[CLAUDE.md:20](../../CLAUDE.md#L20)）；本 Wiki 放在 `docs/code-wiki/`，不改变既有文档结构。

## 非运行时目录

| 目录 | 定位 | 证据 |
| --- | --- | --- |
| `DeepTutor-main/` | 上游参考源码（Apache-2.0），用于核对算法与提示词；被 `.gitignore` 排除，不参与插件运行 | [.gitignore:5](../../.gitignore#L5)、[docs/DESIGN.md:3](../DESIGN.md#L3) |
| `Sources/` | 课程 PDF 示例资料，手工演练用；被 `.gitignore` 排除 | [.gitignore:6](../../.gitignore#L6) |
| `.tutor/` | 运行时用户数据；被 `.gitignore` 排除 | [.gitignore:2](../../.gitignore#L2) |
