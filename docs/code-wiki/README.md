# tutor Code Wiki

> 一个 Claude Code 学习插件：用学习者自己的课程资料建立本地知识库，按学习路径讲解、出题、复习，并把问过的题归档进题库复用。推理全部由 Claude 完成，掌握度、门槛与复习排期由 Python 脚本确定性计算。

## 项目速览

| 维度 | 内容 |
| --- | --- |
| 形态 | Claude Code 插件（5 个技能 + 8 个命令行/库脚本），无常驻服务 |
| 主要语言 | Python 3.9+，仅标准库（[README.md:47](../../README.md#L47)、[README.md:84](../../README.md#L84)） |
| 运行入口 | `skills/*/SKILL.md` 由 Claude 调用；确定性逻辑在 `scripts/*.py` 的 `main` |
| 存储 | 每个学习文件夹内的 `.tutor/`（SQLite FTS5 索引、JSONL 事件日志、路径 JSON、题库 JSON、教学档案 JSON） |
| 代码规模 | `scripts/` 8 文件 4,022 行；`skills/` 5 文件 752 行；测试 6 文件 3,143 行、244 个用例 |
| 版本 | 插件清单仍是 0.3.0（[.claude-plugin/plugin.json:4](../../.claude-plugin/plugin.json#L4)）；0.4.0 功能已合入 `main`，CHANGELOG 已有 0.4.0 草稿（[CHANGELOG.md:3-11](../../CHANGELOG.md#L3-L11)），版本号、tag 与 release 尚未发布 |
| 分析版本 | `607d4cc`（教学档案，已通过 PR 合入远程 `main`；本地 `main` 因 squash 合并与远程分叉，待同步），工作区干净；文档首次生成于 2026-11-09（0.2.0），2026-11-22 先后更新到 0.3.0 与 0.4.0 功能 |
| 验证状态 | 本机 Python 3.13.12：`unittest` 244/244 通过（Python 3.13 与 3.9）、黄金向量变异检查 19/19 杀死、walkthrough 退出码 0（2026-11-22 重跑） |

## 0.4.0 相对 0.3.0 的变化（本次更新范围）

| 变化 | 影响的文档 |
| --- | --- |
| 新增 `scripts/teaching.py`：每个知识库、每位学习者一份教学档案（偏好 + 按知识点的观察），`show`/`list`/`update` | [02](02-modules.md)、[03](03-key-code.md)、[04](04-dependencies.md) |
| 每条记录（含删除）必须带学习者当前消息里的逐字原话；观察只能挂在账本已登记的知识点上；整批全有或全无 | [01](01-architecture.md)、[03](03-key-code.md) |
| `/tutor:learn` 新增 0b（讲解前读档案）、6（记录反馈）、7（列出与遗忘）；其他技能不读写档案 | [01](01-architecture.md)、[02](02-modules.md) |
| 数据布局新增 `.tutor/teaching/<kb>/<learner>.json`；权限示例新增 `teaching.py` | [05](05-running.md)、[06](06-development.md) |
| 测试由 209 增至 244 个用例（`test_teaching.py` 35 个）；`walkthrough.sh` 新增第 12 节 | [02](02-modules.md)、[05](05-running.md) |
| README、NOTICE、CHANGELOG、`learn` 技能的行号因新增内容下移，已重新核对 | 全部 |

## 0.3.0 相对 0.2.0 的变化（上一次更新）

| 变化 | 影响的文档 |
| --- | --- |
| 新增 `scripts/bank.py`：题库（题目、参考题、仿写题）的存取与查重 | [02](02-modules.md)、[03](03-key-code.md)、[04](04-dependencies.md) |
| 事件新增可选字段 `answer_status`（`sourced` / `unverified`），`unverified` 作答不开路径门槛 | [01](01-architecture.md)、[03](03-key-code.md) |
| `/tutor:quiz` 增加题库归档、参考题与仿写题（2b）、题库浏览（2c）；`/tutor:review` 优先取题库题 | [01](01-architecture.md)、[02](02-modules.md) |
| 数据布局新增 `.tutor/question_bank/<kb>.json`；权限示例新增 `bank.py` | [05](05-running.md)、[06](06-development.md) |
| 测试由 166 增至 209 个用例（`test_bank.py` 34 个，账本 +4，路径 +5） | [02](02-modules.md)、[05](05-running.md) |
| `learner_log.py`、`path.py` 的行号因新增代码整体下移，已全部重新核对 | 全部 |

## 快速开始

```bash
# 1. 安装（在任意目录；需要 Claude Code 2.1.275+）
claude plugin marketplace add Huangyu-Shen/deep-tutor-with-claude-code
claude plugin install tutor@deep-tutor-with-claude-code

# 2. 在“学习文件夹”里启动 Claude，而不是在插件仓库里
claude

# 3. 建库后自由使用
/tutor:kb ./lectures
/tutor:learn 什么是卡尔文循环？
```

来源：[README.md:33-40](../../README.md#L33-L40)。三条命令的证据等级见 [05-running.md](05-running.md)。

## 文档导航

| 文档 | 内容 |
| --- | --- |
| [整体架构](01-architecture.md) | 系统定位、入口、组件图、主流程时序、横切关注点、关键设计决策 |
| [模块职责](02-modules.md) | 5 个技能、8 个脚本、测试与打包各自的职责与边界 |
| [关键代码](03-key-code.md) | 掌握度、调度器、事件日志、路径门槛、题库、教学档案、知识库与引文核对的关键类/函数 |
| [依赖关系](04-dependencies.md) | 技能→脚本→数据层依赖图、外部工具、DeepTutor 参考源码的定位 |
| [运行方式](05-running.md) | 环境要求、安装、数据布局、测试与演练命令、常见问题、发布流程 |
| [开发指南](06-development.md) | 模块化约定、数据契约、扩展点、调试与文档沉淀规则 |

## 分析范围与排除项

- **已深入分析**：`scripts/`、`skills/`、`tests/`、`.claude-plugin/`、根目录文档（`README.md`、`CLAUDE.md`、`CONCEPTS.md`、`CHANGELOG.md`）、`docs/` 下的设计与验证记录。
- **未深入**：
  - `DeepTutor-main/`：上游 DeepTutor 的参考源码（Apache-2.0），被 `.gitignore` 排除，不参与插件运行时；只在 [04-dependencies.md](04-dependencies.md) 说明其用途。
  - `Sources/`：课程 PDF 示例资料，被 `.gitignore` 排除，仅作手工演练素材。
  - `tests/fixtures/`：二进制测试夹具（PDF/PPTX），只记录用途，不逐字节分析。
  - `.tutor/`：本机运行时数据（`kb/comsm0126/index.sqlite`），属于用户数据，不纳入文档内容。
  - `.screenshots/`、`.pytest_cache/`、`__pycache__/`：临时产物。
- **未能确认（`待核实`）**：
  - 本地 `main` 与远程 `main` 已分叉（本地多一个 `607d4cc`，远程是它的 squash 合并提交），文档以本地工作区内容为准，两边文件树一致。
  - 0.4.0 的真实技能会话只引用 [docs/verification/0.4.0.md](../verification/0.4.0.md)，本次未独立复现。
  - 插件在非 macOS 平台的行为（[README.md:77](../../README.md#L77) 明确标注未测试）。
  - 发布 tag 与 GitHub Release 的线上状态（0.3.0 的 tag 未核对）。
  - 真实技能会话（`claude -p "/tutor:..."`）本次未重跑；0.3.0 的真实会话结果只引用 [docs/verification/0.3.0.md](../verification/0.3.0.md)，未独立复现。
  - 本次未在 Python 3.9 上重跑测试（验证记录称 3.9.6 通过，本次未复现）。
