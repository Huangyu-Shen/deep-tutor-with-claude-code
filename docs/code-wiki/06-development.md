# 开发指南

> 目的：说明改这个仓库时必须遵守的边界、扩展点与验证门槛。依据以 [CLAUDE.md](../../CLAUDE.md) 与现有代码为准。

## 模块化约定

- 每个技能（`skills/*`）和每个脚本（`scripts/*`）都是独立模块；脚本是小型命令行程序，JSON 进 / JSON 出，可单独运行与测试（[CLAUDE.md:25-28](../../CLAUDE.md#L25-L28)）。
- 命令行脚本之间不互相导入，只通过 `.tutor/` 数据格式和 JSON 接口沟通；纯函数库（`learner_log.py`、`mastery.py`、`scheduler.py`）可被导入（[CLAUDE.md:27](../../CLAUDE.md#L27)）。
- 一个版本只动它需要的模块，不为未来预造抽象层（[CLAUDE.md:28](../../CLAUDE.md#L28)）。

## 数据契约

改任何数据格式前，先确认三处同时更新：

| 契约 | 位置 | 约束 |
| --- | --- | --- |
| 事件类型与字段 | [scripts/learner_log.py:29-37](../../scripts/learner_log.py#L29-L37)、[scripts/learner_log.py:139-184](../../scripts/learner_log.py#L139-L184) | 新增枚举要同步校验、重放与测试；写入必须经 `prepare_event` |
| `paths.json` 结构 | [scripts/path.py:102-148](../../scripts/path.py#L102-L148)、[scripts/path.py:150-220](../../scripts/path.py#L150-L220) | 只存配置；状态不得写回 |
| SQLite schema | [scripts/kb.py:57-73](../../scripts/kb.py#L57-L73) | 改表结构要处理旧库；页面文本与 FTS 同事务写入 |

## 常见扩展点

| 想做的事 | 改哪里 | 注意事项 |
| --- | --- | --- |
| 新增技能 | 新建 `skills/<name>/SKILL.md` | frontmatter 必须有 `name`、`description`、`argument-hint`、`allowed-tools`；只在该技能当轮生效（[CLAUDE.md:33](../../CLAUDE.md#L33)） |
| 新增脚本命令 | 对应脚本的 `parser()` + `run/cmd_*` + `main()` | 保持 `{ok, ...}` 输出与失败退出码 1；不要引入第三方包 |
| 新增知识类型 | [scripts/scheduler.py:26-34](../../scripts/scheduler.py#L26-L34) + `path.py` 的门槛分支 | 同时决定它走数值还是定性门槛（[scripts/path.py:47-51](../../scripts/path.py#L47-L51)） |
| 调整门槛数值 | [scripts/path.py:47-51](../../scripts/path.py#L47-L51) | 改完必须补/改 `tests/test_path.py`，并检查黄金向量是否受影响 |
| 新增文件格式 | `kb.py` 的 `extract()` 分派 | 缺工具时返回安装提示，不要抛裸异常（[scripts/kb.py:120-134](../../scripts/kb.py#L120-L134)） |
| 调整复习算法 | [scripts/scheduler.py](../../scripts/scheduler.py) | 必须重跑黄金向量与变异检查（[CLAUDE.md:35](../../CLAUDE.md#L35)） |

## 测试策略

```bash
python3 -I -W error::ResourceWarning -m unittest discover -s tests   # 166 个用例
python3 -I tests/golden/mutation_check.py                            # 黄金向量变异检查 19/19
bash tests/walkthrough.sh                                           # 端到端脚本演练
```

- 单元测试必须覆盖新规则的正反例；涉及“可信”承诺的代码默认从严，每放宽一处加一条回归测试（[CLAUDE.md:36](../../CLAUDE.md#L36)）。
- 黄金向量由 DeepTutor 1.6.15 原实现生成；移植代码必须逐项复现（[README.md:101](../../README.md#L101)）。
- 技能改动必须用真实会话验证，而不只看单元测试：

  ```bash
  claude -p "/tutor:xxx" --plugin-dir . --output-format json
  ```

  检查 `permission_denials`；真实会话可能把同一道题记两次，去重逻辑必须同时存在（[CLAUDE.md:31](../../CLAUDE.md#L31)、[CLAUDE.md:44](../../CLAUDE.md#L44)）。

## 调试

- 所有脚本的失败都是 `{"ok": false, "error": "..."}` + 退出码 1（[scripts/kb.py:884-902](../../scripts/kb.py#L884-L902)、[scripts/ledger.py:292-304](../../scripts/ledger.py#L292-L304)、[scripts/path.py:1048-1066](../../scripts/path.py#L1048-L1066)）。
- 日志坏行不会中断命令，会出现在 `warnings` 里（[scripts/learner_log.py:84-119](../../scripts/learner_log.py#L84-L119)）；先看 `path.py status` 的 `warnings` 与 `gaps`。
- 查看原始事件：`python3 scripts/ledger.py export --format json`；查看当前状态：`state`；查看到期：`due`。
- 向脚本传 JSON 时，先写到 `.tutor/tmp/` 新文件再 `<` 重定向，不用 heredoc（[CLAUDE.md:32](../../CLAUDE.md#L32)）。

## 文档与工作流

- 新功能按 Compound Engineering 流程：`ce-brainstorm` → `ce-plan`（需用户确认）→ `ce-work` → `ce-code-review` → 修复 → `ce-compound`（[CLAUDE.md:10-23](../../CLAUDE.md#L10-L23)）。
- 可复用经验写入 `docs/solutions/<分类>/`，带 YAML frontmatter（`module`、`tags`、`problem_type`）（[CLAUDE.md:20](../../CLAUDE.md#L20)）。
- 版本规则：次版本加功能，修订版只修缺陷；当前 0.2.0（[CLAUDE.md:7](../../CLAUDE.md#L7)、[CHANGELOG.md:1-12](../../CHANGELOG.md#L1-L12)）。

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
| 调度器是 DeepTutor 的指数遗忘基线，不是校准 FSRS | 复习间隔只能当估计，不能当承诺 | [README.md:73](../../README.md#L73)、[scripts/scheduler.py:13](../../scripts/scheduler.py#L13) |
| 只在 macOS 验证 | 其他平台行为未知 | [README.md:74](../../README.md#L74) |
| 引文核对只覆盖逐字引用 | 转述与页码不被验证 | [README.md:69](../../README.md#L69) |
| 扫描页转写核对是低置信 | 只证明与转写一致 | [README.md:70](../../README.md#L70) |
| 判分由 Claude 完成 | 账本只记录结果，不判分 | [README.md:71](../../README.md#L71) |
| 插件目录不可写 | 学习文件夹必须在别处 | [README.md:38](../../README.md#L38) |
