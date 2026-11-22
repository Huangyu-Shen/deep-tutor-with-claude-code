# 开发指南

> 目的：说明改这个仓库时必须遵守的边界、扩展点与验证门槛。依据以 [CLAUDE.md](../../CLAUDE.md) 与现有代码为准。

## 模块化约定

- 每个技能（`skills/*`）和每个脚本（`scripts/*`）都是独立模块；脚本是小型命令行程序，JSON 进 / JSON 出，可单独运行与测试（[CLAUDE.md:25-28](../../CLAUDE.md#L25-L28)）。
- 命令行脚本之间不互相导入，只通过 `.tutor/` 数据格式和 JSON 接口沟通；纯函数库（`learner_log.py`、`mastery.py`、`scheduler.py`）可被导入（[CLAUDE.md:27](../../CLAUDE.md#L27)）。`bank.py` 与 `teaching.py` 是命令行脚本，都只导入纯库 `learner_log`，符合这条约定。
- 一个版本只动它需要的模块，不为未来预造抽象层（[CLAUDE.md:28](../../CLAUDE.md#L28)）。

## 数据契约

改任何数据格式前，先确认三处同时更新：

| 契约 | 位置 | 约束 |
| --- | --- | --- |
| 事件类型与字段 | [scripts/learner_log.py:29-38](../../scripts/learner_log.py#L29-L38)、[scripts/learner_log.py:140-185](../../scripts/learner_log.py#L140-L185) | 新增枚举要同步校验、重放与测试；写入必须经 `prepare_event` |
| 题库 JSON 结构 | [scripts/bank.py:42-49](../../scripts/bank.py#L42-L49)、[scripts/bank.py:106-137](../../scripts/bank.py#L106-L137) | `schema_version` 只认真正的整数 1；布尔字段只认布尔；读取从严、损坏即报错；改格式要同步技能里的字段清单（[skills/quiz/SKILL.md:81](../../skills/quiz/SKILL.md#L81)） |
| 教学档案 JSON 结构 | [scripts/teaching.py:39-56](../../scripts/teaching.py#L39-L56)、[scripts/teaching.py:98-140](../../scripts/teaching.py#L98-L140) | `schema_version` 只认真正的整数 1；读取从严、损坏即报错；每条记录与删除都要 `evidence`；改字段要同步校验、技能 `learn` 第 6 节里的 JSON 示例和测试（[skills/learn/SKILL.md:154-184](../../skills/learn/SKILL.md#L154-L184)） |
| `answer_status` 字段 | [scripts/learner_log.py:35](../../scripts/learner_log.py#L35)、[scripts/learner_log.py:205-211](../../scripts/learner_log.py#L205-L211)、[scripts/path.py:488-500](../../scripts/path.py#L488-L500) | 写入严格、读取宽容；门槛用白名单；新增取值要同时改写入校验、门槛过滤与测试 |
| `paths.json` 结构 | [scripts/path.py:102-148](../../scripts/path.py#L102-L148)、[scripts/path.py:150-220](../../scripts/path.py#L150-L220) | 只存配置；状态不得写回 |
| SQLite schema | [scripts/kb.py:57-73](../../scripts/kb.py#L57-L73) | 改表结构要处理旧库；页面文本与 FTS 同事务写入 |

## 常见扩展点

| 想做的事 | 改哪里 | 注意事项 |
| --- | --- | --- |
| 新增技能 | 新建 `skills/<name>/SKILL.md` | frontmatter 必须有 `name`、`description`、`argument-hint`、`allowed-tools`；只在该技能当轮生效（[CLAUDE.md:33](../../CLAUDE.md#L33)） |
| 新增脚本命令 | 对应脚本的 `parser()` + `run/cmd_*` + `main()` | 保持 `{ok, ...}` 输出与失败退出码 1；不要引入第三方包；技能里的命令要写全参数（`--kb`、`--question-id`），否则会被权限拒绝（[CLAUDE.md:51](../../CLAUDE.md#L51)） |
| 新增题目字段或题型 | `bank.py` 的 `QUESTION_KEYS`、`validate_question`、`_payload` | 三处同步；`FORMATS` 与技能里的格式名一致（`fill_in_blank`，不是 `fill_in_the_blank`）；改后补 `tests/test_bank.py` |
| 新增知识类型 | [scripts/scheduler.py:26-34](../../scripts/scheduler.py#L26-L34) + `path.py` 的门槛分支 | 同时决定它走数值还是定性门槛（[scripts/path.py:47-51](../../scripts/path.py#L47-L51)） |
| 调整门槛数值 | [scripts/path.py:47-51](../../scripts/path.py#L47-L51) | 改完必须补/改 `tests/test_path.py`，并检查黄金向量是否受影响 |
| 新增文件格式 | `kb.py` 的 `extract()` 分派 | 缺工具时返回安装提示，不要抛裸异常（[scripts/kb.py:120-134](../../scripts/kb.py#L120-L134)） |
| 调整复习算法 | [scripts/scheduler.py](../../scripts/scheduler.py) | 必须重跑黄金向量与变异检查（[CLAUDE.md:35](../../CLAUDE.md#L35)） |

## 测试策略

```bash
python3 -I -W error::ResourceWarning -m unittest discover -s tests   # 244 个用例
python3 -I tests/golden/mutation_check.py                            # 黄金向量变异检查 19/19
bash tests/walkthrough.sh                                           # 端到端脚本演练
```

- 单元测试必须覆盖新规则的正反例；涉及“可信”承诺的代码默认从严，每放宽一处加一条回归测试（[CLAUDE.md:36](../../CLAUDE.md#L36)）。
- 黄金向量由 DeepTutor 1.6.15 原实现生成；移植代码必须逐项复现（[README.md:104](../../README.md#L104)）。
- 技能改动必须用真实会话验证，而不只看单元测试：

  ```bash
  claude -p "/tutor:xxx" --plugin-dir . --output-format json
  ```

  检查 `permission_denials`；真实会话可能把同一道题记两次，去重逻辑必须同时存在（[CLAUDE.md:31](../../CLAUDE.md#L31)、[CLAUDE.md:44](../../CLAUDE.md#L44)）。

## 调试

- 所有脚本的失败都是 `{"ok": false, "error": "..."}` + 退出码 1（[scripts/kb.py:884-902](../../scripts/kb.py#L884-L902)、[scripts/ledger.py:292-304](../../scripts/ledger.py#L292-L304)、[scripts/path.py:1052-1070](../../scripts/path.py#L1052-L1070)）。
- 日志坏行不会中断命令，会出现在 `warnings` 里（[scripts/learner_log.py:85-120](../../scripts/learner_log.py#L85-L120)）；先看 `path.py status` 的 `warnings` 与 `gaps`。
- 查看原始事件：`python3 scripts/ledger.py export --format json`；查看当前状态：`state`；查看到期：`due`。
- 向脚本传 JSON 时，先写到 `.tutor/tmp/` 新文件再 `<` 重定向，不用 heredoc（[CLAUDE.md:32](../../CLAUDE.md#L32)）。

## 文档与工作流

- 新功能按 Compound Engineering 流程：`ce-brainstorm` → `ce-plan`（需用户确认）→ `ce-work` → `ce-code-review` → 修复 → `ce-compound`（[CLAUDE.md:10-23](../../CLAUDE.md#L10-L23)）。
- 可复用经验写入 `docs/solutions/<分类>/`，带 YAML frontmatter（`module`、`tags`、`problem_type`）（[CLAUDE.md:20](../../CLAUDE.md#L20)）。
- 版本规则：次版本加功能，修订版只修缺陷；清单版本 0.3.0，0.4.0 功能已合入但未发布（[CLAUDE.md:7](../../CLAUDE.md#L7)、[CHANGELOG.md:3-11](../../CHANGELOG.md#L3-L11)）。
- 0.4.0 的教训已写进 [CLAUDE.md:53-57](../../CLAUDE.md#L53-L57) 与 [docs/solutions/logic-errors/skill-recording-flow-order-and-single-message-evidence-2026-11-22.md](../solutions/logic-errors/skill-recording-flow-order-and-single-message-evidence-2026-11-22.md)：靠模型触发的记录步骤要放在长回答之前、证据绑定在一条消息时恢复流程也必须在同一条消息里完成、每个技能分支（含被拒绝的）都要真实会话实跑、可被“忘掉”的数据不进只追加日志。
- 0.3.0 的教训已写进 [CLAUDE.md:46-51](../../CLAUDE.md#L46-L51) 与 [docs/solutions/logic-errors/answer-status-fail-closed-gates-tolerant-records.md](../solutions/logic-errors/answer-status-fail-closed-gates-tolerant-records.md)：可选证据字段写严读宽、批量入库不丢整批、JSON 字段类型从严、题干查重要去掉所有空白、技能里的脚本命令写全参数。

## 发布

```bash
git tag -a vX.Y.Z <commit> -m "X.Y.Z"
git push origin vX.Y.Z
gh release create vX.Y.Z --verify-tag --title "vX.Y.Z" --notes-file <文件>
```

notes 取 CHANGELOG 对应段落，与 CHANGELOG 同语言，并链回 CHANGELOG（[CLAUDE.md:19](../../CLAUDE.md#L19)）。

## 已知限制

| 限制 | 影响 | 依据 |
| --- | --- | --- |
| 调度器是 DeepTutor 的指数遗忘基线，不是校准 FSRS | 复习间隔只能当估计，不能当承诺 | [README.md:74](../../README.md#L74)、[scripts/scheduler.py:13](../../scripts/scheduler.py#L13) |
| 只在 macOS 验证 | 其他平台行为未知 | [README.md:76](../../README.md#L76) |
| 引文核对只覆盖逐字引用 | 转述与页码不被验证 | [README.md:70](../../README.md#L70) |
| 扫描页转写核对是低置信 | 只证明与转写一致 | [README.md:71](../../README.md#L71) |
| 判分由 Claude 完成 | 账本只记录结果，不判分 | [README.md:72](../../README.md#L72) |
| AI 仿写题的答案可能是 `unverified` | 计入掌握度/错题/复习，但不开路径门槛；展示时必须标明是 AI 提议 | [README.md:76](../../README.md#L76)、[scripts/path.py:488-500](../../scripts/path.py#L488-L500) |
| 教学档案的读取、记录与告知是技能指令，脚本无法强制 | 讲师可能漏读、漏记或在没有原话时硬记；脚本只拦住“原话不在消息里”，并且消息由 Claude 传入，不能证明学习者真说过 | [scripts/teaching.py:264](../../scripts/teaching.py#L264)、[docs/verification/0.4.0.md](../verification/0.4.0.md) |
| 教学档案的并发与大小写 | 两个会话同时写同一份档案可能丢一次更新（无锁）；大小写不敏感文件系统上 `Bio` 与 `bio` 共用一个文件；不检查知识库是否存在，拼错 `--kb` 会建出游离目录 | [docs/verification/0.4.0.md](../verification/0.4.0.md)（已知局限） |
| 题库只在“归档后再提问”的技能纪律下才完整 | 脚本不能强制技能先归档；未归档的题仍可被问出，只是不会进题库 | [skills/quiz/SKILL.md:91](../../skills/quiz/SKILL.md#L91)、[skills/review/SKILL.md:71](../../skills/review/SKILL.md#L71) |
| 插件目录不可写 | 学习文件夹必须在别处 | [README.md:38](../../README.md#L38) |
