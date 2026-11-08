# 关键类与函数

> 筛选标准：被多处调用、承载核心规则、定义公开契约或作为扩展点。按模块分组，签名与源码一致；行号可直接跳转。未列出的辅助函数（格式化、参数解析、文件遍历）不属于理解系统的关键路径。

## 目录

- [掌握度](#掌握度-scriptsmasterypy)
- [调度器](#调度器-scriptsschedulerpy)
- [事件契约](#事件契约-scriptslearner_logpy)
- [账本](#账本-scriptsledgerpy)
- [学习路径](#学习路径-scriptspathpy)
- [知识库](#知识库-scriptskbpy)
- [调用关系摘要](#调用关系摘要)

## 掌握度 `scripts/mastery.py`

### `compute_mastery(correctness: list[bool | float]) -> float`

- **位置**：[scripts/mastery.py:27-36](../../scripts/mastery.py#L27-L36)
- **职责**：把一串无提示作答结果折算成 0–1 的掌握度。
- **规则**：只取最近 5 次，从旧到新权重 `0.5/0.7/0.85/0.95/1.0`（[scripts/mastery.py:20-22](../../scripts/mastery.py#L20-L22)）；证据数 1 条封顶 0.5、2 条封顶 0.8（[scripts/mastery.py:24](../../scripts/mastery.py#L24)）。
- **调用方**：`ledger.py`、`path.py`（展示用掌握度）。
- **约束**：输入只应包含无提示作答；带提示的记录由调用方过滤（[scripts/learner_log.py:225-227](../../scripts/learner_log.py#L225-L227)）。

## 调度器 `scripts/scheduler.py`

### `class SpacedRepetitionScheduler`

- **位置**：[scripts/scheduler.py:111-419](../../scripts/scheduler.py#L111-L419)
- **职责**：维护每个知识点的复习状态；按知识类型选择间隔序列；给出可提取率、遗忘风险、复习原因与队列。
- **构造**：`SpacedRepetitionScheduler(*, desired_retention: float = DEFAULT_DESIRED_RETENTION)`，默认保留率 0.9（[scripts/scheduler.py:36](../../scripts/scheduler.py#L36)、[scripts/scheduler.py:113-118](../../scripts/scheduler.py#L113-L118)）。
- **状态**：`RepetitionState`（[scripts/scheduler.py:66-80](../../scripts/scheduler.py#L66-L80)）保存稳定度、上次复习、下次到期、连续答对/答错与间隔档位。

### `schedule_review(self, state, knowledge_type, evidence, *, now=None) -> RepetitionState`

- **位置**：[scripts/scheduler.py:200-287](../../scripts/scheduler.py#L200-L287)
- **职责**：根据一次证据更新复习状态：答对升档、答错降档并缩短间隔。
- **输入**：`LearningEvidence`（[scripts/scheduler.py:52-63](../../scripts/scheduler.py#L52-L63)）；`now` 缺省时用证据时间戳。
- **调用方**：`replay`（[scripts/scheduler.py:391-419](../../scripts/scheduler.py#L391-L419)）、`ledger.schedule`（[scripts/ledger.py:116-137](../../scripts/ledger.py#L116-L137)）。
- **副作用**：无 IO，纯状态计算。

### `retrievability(self, state, *, now=None) -> float`

- **位置**：[scripts/scheduler.py:289-294](../../scripts/scheduler.py#L289-L294)
- **职责**：按指数遗忘曲线 `exp(-elapsed / stability)` 估算当前可提取概率，截断到 `[0, 1]`。
- **调用方**：`forgetting_risk`、`review_reason`、队列排序。

### `forgetting_risk(self, state, error_kp_ids, kp_id, *, now=None) -> float`

- **位置**：[scripts/scheduler.py:296-314](../../scripts/scheduler.py#L296-L314)
- **职责**：在可提取率基础上叠加错题加权，得到排序用的风险值。
- **调用方**：`build_review_queue`。

### `build_review_queue(self, states, knowledge_types, error_kp_ids, evidence_log, *, now=None) -> list[ReviewTask]`

- **位置**：[scripts/scheduler.py:352-389](../../scripts/scheduler.py#L352-L389)
- **职责**：为每个知识点生成 `ReviewTask`，附上复习原因与优先级，并按 `review_sort_key` 排序。
- **调用方**：`ledger.build_due`（[scripts/ledger.py:178-196](../../scripts/ledger.py#L178-L196)）。
- **约束**：只生成候选队列；是否到期由调用方用 `due_at <= now` 过滤。

### `replay(self, knowledge_type, evidence, *, now=None, desired_retention=None, initial_state=None) -> RepetitionState`

- **位置**：[scripts/scheduler.py:391-419](../../scripts/scheduler.py#L391-L419)
- **职责**：从零重放一串证据，得到最终状态；可指定初始状态做增量。
- **约束**：`initial_state` 与 `desired_retention` 不能同时给，否则抛 `ValueError`。
- **调用方**：`ledger.replay` 的排期阶段。

## 事件契约 `scripts/learner_log.py`

### `kp_id(kb: str, name: str) -> str`

- **位置**：[scripts/learner_log.py:51-58](../../scripts/learner_log.py#L51-L58)
- **职责**：生成稳定 ID：`<kb>::<规范化名称>`；规范化会去空白、小写、压缩空白（[scripts/learner_log.py:47-48](../../scripts/learner_log.py#L47-L48)）。
- **调用方**：脚本与技能都用它对齐同一个知识点。

### `read_events(path: Path) -> tuple[list[dict], list[str]]`

- **位置**：[scripts/learner_log.py:84-119](../../scripts/learner_log.py#L84-L119)
- **职责**：逐行读取 JSONL，严格解析（拒绝 `NaN`/`Infinity`），跳过坏行并返回警告。
- **返回**：`(events, warnings)`；文件不存在视为空日志。
- **调用方**：`ledger.py`、`path.py`。
- **约束**：读取容忍未知 `origin`（计入掌握度、不开门），写入拒绝（[scripts/learner_log.py:113-117](../../scripts/learner_log.py#L113-L117)）。

### `validate_event(event: dict, defined: set | None) -> None`

- **位置**：[scripts/learner_log.py:139-184](../../scripts/learner_log.py#L139-L184)
- **职责**：校验事件类型、必填字段、枚举、数值范围与引用一致性；不合法直接抛 `ValueError`。
- **调用方**：`prepare_event`、`ledger.record`、测试。

### `prepare_event(raw: dict, existing: list, now: float) -> dict`

- **位置**：[scripts/learner_log.py:187-215](../../scripts/learner_log.py#L187-L215)
- **职责**：补齐 `ts`、`learner`、`kb`、`origin`、`kp` 等默认值，再调用 `validate_event`；是“写入前唯一入口”。
- **约束**：默认 `origin="authored"`；未知来源不能写入。

### `append_line(path: Path, line: str) -> None`

- **位置**：[scripts/learner_log.py:230-241](../../scripts/learner_log.py#L230-L241)
- **职责**：追加一行 JSON 并持久化；若文件末尾缺换行会先补。
- **副作用**：`flush` + `os.fsync`，保证崩溃后日志仍可解析。
- **调用方**：`ledger.run` 的 `record` 分支。

## 账本 `scripts/ledger.py`

### `replay(events: list[dict]) -> dict`

- **位置**：[scripts/ledger.py:63-113](../../scripts/ledger.py#L63-L113)
- **职责**：单次顺序扫描，派生 `kps`（掌握度、作答、错题）与 `errors`（错题状态）。
- **关键规则**：`error` 事件把错题标为 open；之后连续 2 次答对才标为 graduated（[scripts/ledger.py:57](../../scripts/ledger.py#L57)、[scripts/ledger.py:104-112](../../scripts/ledger.py#L104-L112)）。
- **调用方**：`build_state`、`build_due`、`to_markdown`。

### `build_state(events: list[dict], now: float) -> dict`

- **位置**：[scripts/ledger.py:149-175](../../scripts/ledger.py#L149-L175)
- **职责**：返回每个知识点的掌握度、作答次数、最近结果、复习状态与到期时间。
- **调用方**：`ledger.py state` CLI（[scripts/ledger.py:242-250](../../scripts/ledger.py#L242-L250)）。

### `build_due(events: list[dict], now: float, limit: int | None, kb: str | None) -> dict`

- **位置**：[scripts/ledger.py:178-196](../../scripts/ledger.py#L178-L196)
- **职责**：调用调度器生成复习队列，按 `due_at <= now` 过滤，可按知识库筛选并截断。
- **调用方**：`ledger.py due` CLI、`/tutor:review`。

### `run(args, stdin) -> dict`

- **位置**：[scripts/ledger.py:217-263](../../scripts/ledger.py#L217-L263)
- **职责**：CLI 分派；`record` 分支先构造“现有事件 + 新事件”跑一遍重放，通过后才追加。
- **副作用**：仅 `record` 写日志；其余命令只读。

## 学习路径 `scripts/path.py`

### `validate_outline(outline: dict, store: dict, kb: str, conn, defined: dict[str, str])`

- **位置**：[scripts/path.py:338-435](../../scripts/path.py#L338-L435)
- **职责**：校验 Claude 生成的路径大纲：ID 唯一、前置存在、无环、知识点不重复、引文页存在且非空；返回归一化结果与错误/警告。
- **调用方**：`cmd_validate`、`cmd_save`。
- **约束**：引文必须能在索引里找到对应页（[scripts/path.py:294-337](../../scripts/path.py#L294-L337)）。

### `_unique_attempts(attempts: list[dict]) -> list[dict]`

- **位置**：[scripts/path.py:462-479](../../scripts/path.py#L462-L479)
- **职责**：同一 `question_id` 只保留一次；无提示记录优先于带提示重答；无 `question_id` 的原样保留（但后续不计入门槛）。
- **调用方**：`_display_mastery`、`_gate_attempts`。

### `_gate(kp: dict, attempts: list[dict])`

- **位置**：[scripts/path.py:498-536](../../scripts/path.py#L498-L536)
- **职责**：计算知识点门槛：
  - 数值门槛（`memory`/`procedure`）：掌握度 ≥ 0.9、无提示作答 ≥ 3 次、最近 3 次全对、且没有失败的定性证据。
  - 定性门槛（`concept`/`design`）：最近一次定性考核通过。
- **常量**：[scripts/path.py:47-51](../../scripts/path.py#L47-L51)。
- **返回**：`(gate, gaps, qualitative)`；`gaps` 直接用于向用户解释“还差什么”。
- **调用方**：`_kp_status`。

### `_evidence_status(conn, kp: dict) -> list[dict]`

- **位置**：[scripts/path.py:539-558](../../scripts/path.py#L539-L558)
- **职责**：逐条检查引文来源是否仍存在、页面是否仍非空。
- **输出**：每项带 `valid` 与原因；失效项会阻断门槛。

### `_kp_status(kp: dict, attempts: list[dict], evidence: list[dict]) -> dict`

- **位置**：[scripts/path.py:561-575](../../scripts/path.py#L561-L575)
- **职责**：把门槛、展示掌握度、无提示次数、最近结果、定性结果与证据合并成单个知识点的状态对象。
- **调用方**：`_status_payload`。

### `_mastered_at(path: dict, events: list[dict], evidence_ok: dict, order: list[str]) -> dict`

- **位置**：[scripts/path.py:590-613](../../scripts/path.py#L590-L613)
- **职责**：按日志顺序正向重放，找出“模块解锁且所有知识点门槛通过”的最早事件时间。
- **调用方**：`_status_payload`。
- **约束**：没有通过证据的模块不会得到 `mastered_at`。

### `_status_payload(root: str, kb: str, learner: str, path: dict) -> dict`

- **位置**：[scripts/path.py:632-722](../../scripts/path.py#L632-L722)
- **职责**：路径状态的唯一装配点：模块状态（`locked`/`available`/`in_progress`/`mastered`）、锁定原因、失效证据、下游影响、下一动作。
- **调用方**：`cmd_status`、`cmd_resolve`。
- **约束**：只读，不写回 `paths.json`。

### `cmd_resolve(args) -> dict`

- **位置**：[scripts/path.py:943-987](../../scripts/path.py#L943-L987)
- **职责**：回答“现在能否对某个模块/知识点执行某个动作”，返回 `allowed`、`reason` 与相关状态。
- **原因枚举**：无活动路径、模块锁定、证据失效、已掌握、已通过、未知模块、不在路径内等。
- **调用方**：`/tutor:learn`、`/tutor:quiz`、`/tutor:path`。

## 知识库 `scripts/kb.py`

### `extract(path: Path, force_scanned: bool = False)`

- **位置**：[scripts/kb.py:247-257](../../scripts/kb.py#L247-L257)
- **职责**：按扩展名分派：PDF 走 `extract_pdf`，Office/HTML 走 `extract_markitdown`，纯文本走 `extract_plain`。
- **调用方**：`prepare_file`。
- **异常**：不支持的类型抛 `ValueError`。

### `prepare_file(conn, path: Path, force_scanned: bool = False) -> dict`

- **位置**：[scripts/kb.py:331-346](../../scripts/kb.py#L331-L346)
- **职责**：在事务外完成耗时的抽取/哈希/扫描判定，返回待写入的准备结果；内容未变时返回 `unchanged`。
- **调用方**：`cmd_ingest`。

### `cmd_ingest(args) -> dict`

- **位置**：[scripts/kb.py:392-428](../../scripts/kb.py#L392-L428)
- **职责**：遍历文件，逐个“准备 → 写入”，单文件一个事务；汇总每个文件的动作与读取/未读取页。
- **副作用**：写 `sources`、`pages`、`pages_fts`。
- **调用方**：`/tutor:kb`。

### `search_one(conn, query, mode, limit, source_id)`

- **位置**：[scripts/kb.py:517-579](../../scripts/kb.py#L517-L579)
- **职责**：单查询检索：FTS5 匹配 + 中文回退扫描；`all` 模式要求命中全部词。
- **返回**：命中页、片段、来源显示名与页码；用于讲解选材。
- **调用方**：`cmd_search`。

### `check_quote(conn, item: dict) -> dict`

- **位置**：[scripts/kb.py:776-818](../../scripts/kb.py#L776-L818)
- **职责**：核对引文是否真实出现在指定页：
  - 规范化：统一引号、空白、连字符，支持省略号分段（[scripts/kb.py:690-734](../../scripts/kb.py#L690-L734)）。
  - 状态：`verified`、`verified_case_insensitive`、`verified_elided`、`wrong_page`、`not_found`、`too_short`、`invalid`、`unknown_source`、`unknown_page`；扫描页转写额外给 `low_confidence`（[scripts/kb.py:797](../../scripts/kb.py#L797)）。
- **调用方**：`cmd_verify`、`/tutor:learn`、`/tutor:quiz`。
- **约束**：过短引文不允许作为证据；只做本地核对，不访问网络。

### `cmd_verify(args, stdin) -> dict`

- **位置**：[scripts/kb.py:821-838](../../scripts/kb.py#L821-L838)
- **职责**：从 stdin 读取 `{"quotes": [...]}`，逐条调用 `check_quote` 并汇总。
- **调用方**：`/tutor:learn`、`/tutor:quiz` 的引用核对步骤。

### `cmd_render(args) -> dict` / `cmd_register(args, stdin) -> dict`

- **位置**：[scripts/kb.py:612-635](../../scripts/kb.py#L612-L635)、[scripts/kb.py:638-675](../../scripts/kb.py#L638-L675)
- **职责**：渲染扫描页为 PNG 供 Claude 读图；把 Claude 的转写按页登记为 `ai-transcription`，后续检索与核对复用。
- **约束**：渲染结果落在 `.tutor/kb/<name>/scans/<source_id>/`（[scripts/kb.py:435-441](../../scripts/kb.py#L435-L441)）。

## 调用关系摘要

```mermaid
graph LR
  LEARN["/tutor:learn"] --> STATUS["path.py status"]
  LEARN --> SEARCH["kb.py search"]
  LEARN --> VERIFY["kb.py verify"]
  LEARN --> RECORD["ledger.py record"]

  QUIZ["/tutor:quiz"] --> RESOLVE["path.py resolve"]
  QUIZ --> SEARCH
  QUIZ --> VERIFY
  QUIZ --> RECORD

  REVIEW["/tutor:review"] --> DUE["ledger.py due"]
  REVIEW --> RECORD

  PATH["/tutor:path"] --> SAVE["path.py save/validate"]
  PATH --> STATUS

  STATUS --> PAYLOAD["_status_payload"]
  PAYLOAD --> GATE["_gate"]
  PAYLOAD --> MASTERED["_mastered_at"]
  PAYLOAD --> EVID["_evidence_status"]

  RECORD --> VALIDATE["validate_event / prepare_event"]
  RECORD --> REPLAY["ledger.replay"]
  DUE --> REPLAY
  REPLAY --> SCHED["SpacedRepetitionScheduler"]
  REPLAY --> MAST["compute_mastery"]

  SEARCH --> FTS["SQLite FTS5"]
  VERIFY --> QUOTE["check_quote"]
  STATUS --> EVENTS["events.jsonl"]
  RECORD --> EVENTS
```
