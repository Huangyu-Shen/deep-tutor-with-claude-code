## 项目原则
- 目标(2026-11-22 起):逐模块 1:1 复刻 DeepTutor,与原项目的区别只有一条——推理由 Claude Code 完成,不再自己配置大模型。(此前的目标是"更深入,不是更大",已取消。)
- 复刻的范围边界:需要其他模型的功能(向量/图检索、图像/视频/语音生成、TTS/STT)暂不做;没有常驻服务的形态下无法成立的功能(Web 界面、多用户、IM 伙伴、定时任务)不做;上游靠后台自动做的事,在插件里改成用户显式命令或技能收尾提示,算系统性的有意偏离。
- 每个模块的复刻流程:先读上游源码,写 `docs/upstream-behavior/<模块>.md` 行为清单(每条标源码文件和行号,分确定性/LLM 判断/依赖基础设施,并标注插件形态与"有意偏离"),你确认后再进入 Compound Engineering 流程写代码;验收按清单逐条:确定性部分用黄金向量(可取自上游自带测试),提示词驱动的部分用真实会话。已有的更严格规则(错题连续答对 2 次、路径门槛、引文逐字核对、教学档案要原话)保留,清单里标"有意偏离"。
- 以 DeepTutor-main/ 源码与官方 SKILL.md 为一手依据;社区项目只作参考,不当事实。
- 所有教学方法的设计要能指回 DeepTutor 的具体文件;结论要有外部可核对的证据(许可证文本、测试、可运行脚本)。
- 全部推理由 Claude 完成,不调用其他模型 API;保留 DeepTutor 的 Apache 2.0 版权声明与出处。
- 先给结构与流程设计,用户确认后再写代码。
- 版本:从 0.1.0 起步。次版本(0.X.0)加新功能,修订版(0.X.Y)只做缺陷修复、维护、性能优化。路线图见 docs/DESIGN.md 第 6 节(2026-11-22 起按 1:1 复刻重排)。
- 讲解、出题、反馈的语言跟随用户当前使用的语言(2026-10-24 起;此前默认英文,已取消);引文保持原文语言、不翻译。用户数据放各项目内的 `.tutor/`。

## 开发流程(Compound Engineering)
每个次版本、每项新功能按以下顺序走,直接调用已安装的 `compound-engineering` 插件的命令(用户级安装,版本 3.32.0):
1. Brainstorm → `ce-brainstorm`
2. Plan → `ce-plan`;**Plan 须经用户确认后才开始 Work**
3. Work → `ce-work`
4. Review → `ce-code-review`:由干净上下文的审查者针对本次改动审查,不由写代码的上下文自评
5. 按 Review 结果修复(必要时用 `ce-debug`)
6. Compound → `ce-compound`:记录学到了什么、哪里出过错、下次怎么避免;可复用的教训提炼进本文件
- 不用 `lfg`(会一路跑到开 PR 并推送,绕过"Plan 先确认")。不调用 `ce-commit`、`ce-commit-push-pr`,除非用户明确说要提交或推送。
- 发布:每个版本一个 release commit 直接进 `main`(0.1.3 起的惯例,不建 feature 分支),然后打附注 tag 并建 release:`git tag -a vX.Y.Z <commit> -m "X.Y.Z" && git push origin vX.Y.Z && gh release create vX.Y.Z --verify-tag --title "vX.Y.Z" --notes-file <文件>`。notes 取 CHANGELOG 对应段落、与 CHANGELOG 同语言(英文),并链回 CHANGELOG。从 0.1.4 起纳入快照,0.1.0–0.1.3 未打 tag。
- 文档位置(已核实):`ce-*` 的产物根目录默认是 `docs/`(未设 `docs_root`),`ce-compound` 写到 `docs/solutions/<分类>/`,`ce-plan` 用 `docs/plans/`。0.1.0 的旧文档保持原位(`docs/brainstorms/`、`docs/plans/`、`docs/compound/`、`docs/verification/`);新的可复用经验一律写进 `docs/solutions/`,按分类目录组织,每篇带 YAML frontmatter(`module`、`tags`、`problem_type`),开工前先翻一遍相关分类。共享领域词汇见根目录 `CONCEPTS.md`(实体、命名流程、状态概念),理解代码库或讨论领域概念时可先看。
- 若某个 `ce-*` 命令因缺少 `bun` 等外部工具失败,告诉用户,不要自己安装。
- 按任务大小取舍:错别字、单行修复、纯文档微调不走全流程;修订版(0.X.Y)缺陷修复至少要有复现测试和 Review。
- 代价是更多 token,换来的是计划被确认、审查独立、教训沉淀;它不保证零缺陷,测试与黄金向量才是正确性的依据。

## 模块化
- 每个技能(skills/*)和每个脚本(scripts/*)单独成模块,脚本是小型命令行程序,JSON 进 JSON 出,可单独运行和测试。
- 命令行脚本(`kb.py`、`ledger.py`)之间只通过两个契约沟通:`.tutor/` 下的数据格式和脚本的 JSON 接口,不互相导入。纯函数库(`mastery.py`、`scheduler.py`)可以被命令行脚本导入。
- 每个模块自带测试;一个版本只动它需要的模块。不为未来版本预先造抽象层。

## 经验教训(0.1.0 Compound,详见 docs/compound/0.1.0.md)
- 技能写完后必须用真实会话跑一遍再算完成:`claude -p "/tutor:xxx" --plugin-dir . --output-format json`,看 `permission_denials`。
- 向脚本传 JSON 或文本时,先用 Write 写进 `.tutor/tmp/` 下的新文件,再用 `<` 重定向;不用 heredoc(含大括号和引号会被 Claude Code 拦截)。
- 可用的权限写法:`Bash(python3 *scripts/x.py*)`、`Edit(**/.tutor/tmp/*)`;`Write(...)` 规则不匹配绝对路径。技能的 `allowed-tools` 只在该技能的那一轮生效。
- 任何关于 DeepTutor 行为的说法,必须在 `DeepTutor-main/` 里查到文件和行号再写。
- 黄金向量必须配变异检查(`tests/golden/mutation_check.py`);改调度器后要重跑。
- 支撑"可信"承诺的代码(引文核对、掌握度)默认从严,每放宽一处就加一条回归测试。
- 我自己的 Bash 命令在 zsh 下:不要依赖 `$VAR` 按空格拆词,用函数或 bash 脚本。

## 经验教训(0.2.0 Compound,详见 docs/solutions/architecture-patterns/derive-path-gates-by-replaying-the-attempt-log.md)
- 学习路径状态只能由 `scripts/path.py` 从事件日志重放得出,不写进 `paths.json`;技能只渲染 `status`/`resolve` 的 JSON。
- 同一 `question_id` 只算一次门槛证据;带提示的重答不能覆盖之前的无提示记录;无 `question_id` 的 attempt 只计入掌握度展示。
- `diagnostic` 不开门,`exemption` 可开门但失败后模块保持 available;定性失败会阻断同一知识点的数值门槛。
- 引文页面可能在保存后变空或消失;证据失效要连同下游 `invalidated_by` 一起报告,而不是继续报 mastered。
- 真实技能会话会把同一道题记两次(单元测试只记一次);门槛去重与"只记一次"的指令必须同时存在,并用真实会话验证。

## 经验教训(0.3.0 Compound,详见 docs/solutions/logic-errors/answer-status-fail-closed-gates-tolerant-records.md)
- 可选证据字段(如 `answer_status`)写入时严格校验,读取时宽容;门槛用白名单(缺失或 `sourced`),未知值和坏类型一律不开门,但仍计入掌握度、错题和复习。
- 批量入库不要因一道重复题丢掉整批:干净题照常写入,重复题放 `rejected`/`near_duplicates`;只有结构错误(字段非法、同 id 不同内容、批内重复 id)才整批失败且不写盘。
- JSON 字段类型从严:`schema_version` 只接受真正的 int(不接受 `true`/`1.0`),布尔字段只接受布尔(字符串 `"false"` 不是 false),`citation_ok` 必须恰为 `true` 才能被取用。
- 题干查重归一化要去掉所有空白(含换行、制表),否则同一道题会以两个 id 入库,门槛各算一次证据。
- 技能里的脚本命令要写全参数(`--kb`、`--question-id`),否则会被权限拒绝;独立小节要先自己取 `<stamp>` 和知识点 id;浏览题库、恢复失效引文、排除已用题都要有明确步骤。

## 经验教训(0.4.0 Compound,详见 docs/solutions/logic-errors/skill-recording-flow-order-and-single-message-evidence-2026-11-22.md)
- 靠模型自觉触发的记录步骤要放在长回答之前:一轮会话讲完就结束,放在末尾会被悄悄跳过;只有真实 `claude -p` 会话能暴露这类遗漏,单元测试测不到。
- 脚本把证据绑定在"当前这条消息"时,每个拒绝分支的恢复流程都必须在同一条消息里完成(例如满额时让学习者在同一条回复里同时指明要删的和重述新的,发一批 remove + upsert)。
- 每个技能分支都要实跑,包括被脚本拒绝的分支:先造出触发状态(如先填满档案),再同时看回复和落盘文件。
- 学习者能"忘掉"的记录不能放进只追加的事件日志;可删除的数据用单独文件,读取从严、损坏即报错,写入全有或全无。


## 经验教训(0.5.0 Compound,详见 docs/solutions/logic-errors/rewrite-from-parsed-model-must-round-trip-or-refuse-2026-11-22.md)
- 凡是"读文件 → 解析成模型 → 改模型 → 整个写回"的存储:写入前校验每个被接受的值都能被下一次解析原样读回(单行、不含注释标记、`\Z` 而不是 `$`),并拒绝重写解析器会丢行的文件(提示改用整篇覆盖);拒绝的批次必须用"非规范格式但能完整解析"的文件证明字节不变。
- 上游黄金向量只证明与上游在上游测过的输入上一致,不证明上游接受后处理错的输入是对的;额外的校验写成"有意偏离",进 NOTICE 和行为清单。
- 测试基类里不要给辅助方法起名 `fail`、`run`、`id` 等 `TestCase` 已有的名字:`assertEqual` 失败时会调用 `self.fail`,被覆盖后失败的字符串断言会悄悄通过。变异检查里"该死的变异体活下来"是发现这类问题的信号。
- 新增的技能步骤会挤掉已有的记录步骤(0.5.0 加了记忆步骤后,第一次真实会话漏记了教学偏好):把"先检查学习者是否在说教学要求"放在新步骤列表的第 1 条,并重跑受影响的真实会话。
- 记忆里"删掉一条"只删文档里的条目,原话还在事件日志和课堂摘要里:技能和 README 必须直说,不要让学习者以为已经清除。
- 评审意见我自己合并时也要逐条回到源码核对;shell 里 `test A = B && echo ok` 在 `set -e` 下失败不会退出,断言要写成 `|| { echo FAILED; exit 1; }`。
