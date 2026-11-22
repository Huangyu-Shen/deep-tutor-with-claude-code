# 上游行为清单:记忆系统(L1 / L2 / L3)

> 状态:**已确认第 6 节的 4 个决定(2026-11-22)**;标"只读了头部"的条目在实现前仍要先读全源码并补全。依据 `DeepTutor-main/`(DeepTutor 1.6.15)源码。每条都标了上游文件与行号;读过什么、没读什么见第 1 节。"插件形态"一栏是我的**提案**,不是已定决定。

## 1. 读法与可信度

**已逐行读过**(行为可信):`services/memory/__init__.py`、`paths.py`、`ids.py`、`document.py`、`trace.py`、`ops.py`、`store.py`、`recall.py`、`settings.py`、`consolidator/__init__.py`、`consolidator/guards.py`,以及 `consolidator/prompts/en/` 下的 `update_l2`、`update_l3`、`audit_l2`、`dedup` 四份提示词、`tools/builtin/__init__.py:1129-1260` 里的 `read_memory`/`write_memory`。

**2026-11-22 补读后,以下也已逐行读过**:`consolidator/modes/update.py`、`dedup.py`、`merge.py`、`audit.py`(L2 部分 `:127-258`)、`line_doc.py`、`chunker.py`、`references.py`、`meta.py`,以及 `snapshot/entity.py`、`diff.py`、`store.py`、`adapters.py:400-690`(`kb`/`chat`/`quiz` 三个 surface 的适配器与分派)。

**仍只读了文件头或函数清单**:`runs.py`(运行管理,已定不做)、`modes/audit.py` 的 L3 部分(`:264-404`)、`modes/_runtime.py`(LLM 调用封装与检查点写入,只看了函数清单)。

**没读**:`snapshot/adapters.py` 里 `notebook`/`cowriter`/`book`/`partner` 四个适配器(`:1-399`,本版不启用这几个 surface)、`api/routers/memory.py`(工作台接口)、`services/rag/service.py:236` 发出 `kb` 查询事件的上下文、`services/learning_journal`、`services/persona`、`multi_user/learner_profile`、`tools/partner_memory.py`、`capabilities/mastery/memory.py`(另一套"辅导记忆",是会话摘要,不是 L1–L3)、`services/suggestions.py:318` 对记忆的使用。

**上游自带测试**:`tests/services/memory/` 下 20 个测试文件、158 个 `test_` 函数(`test_document.py`、`test_ops.py`、`test_ids.py`、`test_chunker.py`、`test_line_doc.py`、`test_references.py`、`test_meta_settings.py`、`test_recall.py`、`test_merge.py` 等),是黄金向量的现成来源。

**一处疑似遗留**:`services/memory/prompts/en.yaml`(156 行,`system_l2`/`user_l2` 等,七个 surface)在 Python 里没有任何引用(`grep` 无命中),实际使用的是 `consolidator/prompts/<语言>/*.yaml`(`modes/_runtime.py:38-50` 的 `load_prompt`)。待核实,暂不当作行为依据。

**分类说明**:`D` = 确定性(可用黄金向量,脚本实现);`L` = 由 LLM 做判断(在插件里由 Claude 做,脚本只做校验与落盘);`I` = 依赖上游的基础设施(服务、界面、多用户),插件里需换形态或不做。

## 2. 总体结构

三层记忆(`services/memory/__init__.py:1-13`):
- **L1** 原始事件:追加式 JSONL,每个 surface 每个 UTC 日期一个文件。
- **L2** 每个 surface 一份 Markdown 摘要。
- **L3** 跨 surface 的 Markdown 综合,四个槽位。
- `ops` 做增删改的批量应用,`consolidator` 用 LLM 把 L1→L2、L2→L3 整合。

存储布局(`paths.py:3-8`):`trace/<surface>/<YYYY-MM-DD>.jsonl`、`L2/<surface>.md`、`L3/<recent|profile|scope|preferences>.md`、`backup/<时间戳>/`。surface 共 7 个:`chat`、`notebook`、`quiz`、`kb`、`book`、`partner`、`cowriter`(`paths.py:48-56`);L3 槽位 4 个(`paths.py:57`)。

## 3. 行为清单

### 3.1 标识符(`ids.py`)

| ID | 上游行为 | 位置 | 类 | 插件形态(提案) |
|---|---|---|---|---|
| M1 | 标识符是 26 位 Crockford base32 的 ULID 风格:前 10 位毫秒时间戳,后 16 位随机,按时间自然排序 | `ids.py:3-10,18-50` | D | 保持;时间戳与随机部分分开测 |
| M2 | trace id 形如 `<surface>:<ULID>`,条目 id 形如 `m_<ULID>`,各有校验正则 | `ids.py:23-24,53-66` | D | 保持,黄金向量用 `tests/services/memory/test_ids.py` |
| M3 | 引用(ref)有四种合法形式:条目 id、trace id、快照引用(`surface:任意实体 id`)、L3 用的裸 surface 名(白名单) | `ids.py:25-36,69-80` | D | 保持 |

### 3.2 文档格式(`document.py`)

| ID | 上游行为 | 位置 | 类 | 插件形态 |
|---|---|---|---|---|
| M4 | L2/L3 是 Markdown:`# 标题`、`## 小节`、`- 文本 [^1], [^3] <!--m_xxx-->`;脚注编号按全文首次出现顺序分配,引用相同来源的条目共用一个编号 | `document.py:3-22,185-232` | D | 保持;纯函数,易做黄金向量 |
| M5 | 条目 id 以 HTML 注释形式附在行尾,往返解析后不丢 | `document.py:24-29,50-52` | D | 保持 |
| M6 | 解析同时接受旧格式(行尾 `[^m_xxx]`、脚注行 `[^m_xxx]: ref1, ref2`),下次保存时迁移到新格式 | `document.py:26-29,53-58,167-180` | D | 不需要(插件没有旧数据),但读取兼容可保留,待你定 |
| M7 | `serialize(parse(x))` 对任何由 `serialize` 产生的文档是幂等的;空小节不输出 | `document.py:31-34,208-209` | D | 保持,用往返测试证明 |

### 3.3 增删改(`ops.py`)

| ID | 上游行为 | 位置 | 类 | 插件形态 |
|---|---|---|---|---|
| M8 | 三种操作:`add`(小节、文本、refs)、`edit`(目标 id、新文本、新 refs)、`delete`(目标 id、原因) | `ops.py:23-46` | D | 保持 |
| M9 | 校验:文本 1–240 字符,小节名 1–80 字符,add/edit 的 refs 非空且每个合法,目标 id 必须存在,删除原因只能是 `contradicted`/`superseded`/`stale`/`low-signal` | `ops.py:18-20,68-117` | D | 保持 |
| M10 | 一批操作整体校验,任何一条失败就整批拒绝、文档不动;同一批里对同一个 id 同时 edit 和 delete 也整批拒绝 | `ops.py:3-6,99-102,111-114,120-129` | D | 保持,与 0.3.0/0.4.0 的"整批全有或全无"同一思路 |
| M11 | 新增条目由系统分配 id,不由调用方指定 | `ops.py:133-138` | D | 保持 |

### 3.4 L1 事件追踪(`trace.py`)

| ID | 上游行为 | 位置 | 类 | 插件形态 |
|---|---|---|---|---|
| M12 | 事件字段:`id`、`ts`(UTC ISO)、`surface`、`kind`、`payload`、可选 `session_id`、`turn_id` | `trace.py:35-63` | D | 保持 |
| M13 | 追加永不抛异常:失败只记日志并吞掉,不能因为记录失败而打断产生它的功能 | `trace.py:1-7,66-79` | D | 保持这个契约;但与我们"写入严格"的原则有张力,待你定 |
| M14 | 每个 surface 每天一个文件,按 UTC 日期分;同一 surface 内串行写 | `trace.py:1-7,66-86`、`paths.py:73-74` | D | 保持文件布局;插件是一次性进程,不需要进程内锁,用 `O_APPEND` 追加 |
| M15 | 按时间读取(可选起始时间,跳过更早的日期文件,坏行跳过),按 id 反查事件(跨 surface),计数,取最新时间戳 | `trace.py:89-153` | D | 保持 |

### 3.5 L1 快照(`snapshot/`)

| ID | 上游行为 | 位置 | 类 | 插件形态 |
|---|---|---|---|---|
| M16 | 快照是"现在工作区里有什么实体"(会话、笔记记录、测验尝试、文档、书、知识库…),各带标签与时间戳;`read_stamps` 只给身份/标签/指纹/时间,不读内容 | `snapshot/__init__.py:1-18,36-42` | D+I | **需要重新设计**:上游每个 surface 的实体由 `adapters.py` 决定,我没读;插件里对应的实体是知识库来源与页面、题库、账本作答等 |
| M17 | `refresh_snapshot` 重新读取工作区、与上次持久化的指纹做差,把变化追加到 `changes.jsonl`,保存新状态;没有变化就什么都不写(幂等) | `snapshot/__init__.py:74-91` | D | 保持 |
| M18 | `pending_changes` 是只读的差异预览 | `snapshot/__init__.py:45-71` | D | 保持 |
| M19 | 分页读取变更历史(新的在前),读取当前状态,清空变更 | `snapshot/__init__.py:94-108` | D | 保持 |

### 3.6 L2 / L3 整合(`consolidator/`)

| ID | 上游行为 | 位置 | 类 | 插件形态 |
|---|---|---|---|---|
| M20 | 三种 LLM 模式:`update`(增量提取事实)、`audit`(对照原始证据逐行修改)、`dedup`(整篇去重,最多若干轮);外加不用 LLM 的 `merge`(只整理脚注) | `consolidator/__init__.py:1-31` | L+D | 判断由 Claude 做,落盘、校验、分块、去重逻辑由脚本做 |
| M21 | "自上次更新以来的新内容"靠 `*.meta.json` 里已见 id 集合做差集,不靠修改时间;文件原子写 | `consolidator/meta.py:1-50`、`modes/update.py:1-20` | D | 保持 |
| M22 | 分块:按字符预算切块,右边界向后延伸到下一个段落或句子边界,从不在句中截断;相邻块按比例重叠 | `chunker.py:1-14,43`、`settings.py:49-53` | D | 保持;但上游块上限是给模型上下文用的,插件里 Claude 的上下文更大,块大小默认值待你定 |
| M23 | update 提示词:每块一次调用,返回 `{"facts": [{text≤240, section, refs}]}`;每条事实至少一个 ref,ref 只能来自本块出现的实体标记;没有可提取的内容就返回空列表,这是正确答案 | `prompts/en/update_l2.yaml:7-45` | L | 提示词整体沿用,改成技能指令,保留 Apache 署名 |
| M24 | L2 update 按 surface 给不同的"关注点":chat 关注误解与掌握,quiz 关注错题模式,kb 关注文档兴趣与查询,等等 | `services/memory/prompts/en.yaml:124-146`(疑似遗留文件,待核实是否被使用) | L | 待核实后再定;若有效则沿用 |
| M25 | L3 update 提示词:引用的是 L2 **文件**(裸 surface 名),不是 L2 条目;措辞必须带数量或 surface 的限定("跨 N 次 chat 交互,用户…") | `prompts/en/update_l3.yaml:3-7,24-33` | L | 沿用 |
| M26 | L3 客观性守卫:禁用绝对化措辞(deeply、truly、mastered、expert in、passionate、loves、hates、always、never、fully understands 及中文对应词),引号内的逐字引用除外 | `guards.py:20-47,65-79,87-104` | D | 保持,这是纯函数,可做黄金向量;与我们"不由 Claude 宣布掌握"的原则一致 |
| M27 | audit 提示词:带行号的块,每条记录附完整原始来源,模型只能返回 `replace`/`delete`/`insert` 三种行编辑,没问题就返回空列表,"不要为改而改" | `prompts/en/audit_l2.yaml:3-42` | L | 沿用 |
| M28 | dedup 提示词:只允许 `replace` 和 `delete`,不能 `insert`;合并时保留质量较高的一条并取 refs 并集,逐字重复的保留靠前的 | `prompts/en/dedup.yaml:3-37` | L | 沿用 |
| M29 | 行编辑格式与应用(`ReplaceLineOp`/`DeleteLinesOp`/`InsertAfterOp`、按行号倒序应用、清理空小节) | `line_doc.py:47-125,172-200,404-440` | D | 保持;我只读了函数清单,实现细节要先读再写 |
| M30 | 引用池校验:每条事实的 ref 必须在本块允许的集合里,不合法的丢弃(`drop_invalid_refs`),缺 ref 的拒绝(`enforce_required`) | `references.py:1-20,125`、`settings.py:57-60` | D | 保持;我只读了头部说明和函数清单 |
| M31 | 默认参数:update/audit 的 L2 预算 20、L3 预算 10,dedup 迭代 3 次且更新后自动去重,merge 在 update/audit/dedup 后自动执行,块重叠 10%,块字符数 1000–64000;越界值被夹回合法范围,坏 YAML 不会让运行崩溃 | `settings.py:21-69,99-184` | D | 保持默认值与夹取规则;"自动"改成"提示用户" |
| M32 | 每轮循环有工具预算(读实体 30、搜索 20、加/改/删各 12 等),超出后给模型一个提示而不是执行 | `guards.py:50-62` | D+L | 待读 `update.py` 的循环后决定是否需要 |
| M33 | 运行管理:同一 `(层, key)` 同时只能有一个活动运行;可取消、可重新连接取回事件、撤销检查点;运行历史最多 200 条 | `runs.py:1-22,47-118` | I | **不做**(需要常驻进程);撤销用"整合前自动备份"代替,待你定 |
| M34 | `preferences` 槽位不参与自动整合;调用整合会直接报错 | `store.py:154-155` | D | 保持 |

### 3.7 读写入口(`store.py`、`recall.py`、工具)

| ID | 上游行为 | 位置 | 类 | 插件形态 |
|---|---|---|---|---|
| M35 | `read_memory` 工具:读取四个 L3 文档拼接成一份,用 `---` 分隔;全空时返回固定提示。工具描述要求"用来个性化语气、深度、例子,不要每轮都读,也不用于纯事实问题" | `store.py:94-103`、`tools/builtin/__init__.py:1129-1155` | D+L | 保持,做成技能里的读取命令 |
| M36 | `read_memory` 只有在用户已有任何 L3 内容时才挂载给对话;读取出错时按"没有记忆"处理 | `agents/_shared/tool_composition.py:299-313` | D | 保持:技能先查概览,空则跳过 |
| M37 | 会话回合里,只有用户明确选了记忆引用时才把 L3 拼接内容放进上下文 | `services/session/turns/executor.py:301,571-572` | I | 插件形态:技能在需要个性化时才读,与 M35 一致 |
| M38 | `write_memory` 工具:只在用户**明确**表达偏好时调用,不得猜测;`op` 为 `add` 或 `edit`,文本 ≤240,`edit` 必须带 `target_id`;先写一条 `preference_stated` 的 L1 事件,让偏好的脚注指向真实事件 | `tools/builtin/__init__.py:1159-1235` | D+L | 保持 |
| M39 | 偏好新增是幂等的:同一小节里"去空白、不分大小写"后相同的文本只会命中已有条目并返回 `duplicate`,不会重复追加(来自 issue #647:引导式学习里模型会跨回合重复写同一偏好) | `store.py:35-51,215-235`、`tools/builtin/__init__.py:1247-1251` | D | 保持;与我们 0.4.0 教学档案里"相同内容是空操作"一致 |
| M40 | 偏好写入固定落在 L3 `preferences.md` 的 `Preferences` 小节,原子写 | `store.py:195-258` | D | 保持 |
| M41 | 概览:每个 L2 surface 和 L3 槽位一行:是否存在、更新时间、条目数、积压(自上次更新以来的 L1 事件数,L3 恒为 0) | `store.py:59-67,262-306` | D | 保持,做成概览命令 |
| M42 | 用户手动覆盖整篇文档、按 id 删除单条 | `store.py:107-122` | D | 保持,用命令 |
| M43 | "预览→应用"两步:先以 `apply_ops=False` 产出操作让用户看,再用 JSON 批量应用 | `store.py:125-193` | D | 保持,正好对应"用户显式命令"的触发方式 |
| M44 | 近期回顾只读"邮票"(标签+时间,不读正文):`recent`(工作区里最近碰过什么)和 `recent_queries`(知识库里最近问过什么,用的是学习者原话);每条带 `days_ago`(整天数,未来时间戳记为 0);同一 surface 同一标签去重只留最新;没有可用时间戳的项直接丢弃;默认 7 天、20 条、上限 100,标签 ≤200 字符 | `recall.py:1-22,35-42,80-136,142-186,198-234` | D | 保持,纯函数,黄金向量用 `test_recall.py` |
| M45 | 删除 L2/L3 单条:按 id 找到并原子写回 | `store.py:113-122` | D | 保持 |

### 3.8 不进入插件范围的上游行为(提案:不做)

| ID | 上游行为 | 位置 | 理由 |
|---|---|---|---|
| M46 | v1 双文件记忆迁移到 v2、`tutorbot`→`partner` 重命名迁移 | `store.py:328-426` | 插件没有历史数据 |
| M47 | 多用户路径覆盖(合作伙伴运行时借用管理员的记忆目录) | `paths.py:28-46,63-66` | 单用户 |
| M48 | 进程内 asyncio 锁、写锁表 | `trace.py:24-32`、`store.py:72-73,319-325` | 一次性进程;用原子写 |
| M49 | 运行管理器、事件回放、撤销检查点(M33) | `runs.py` | 需要常驻进程 |
| M50 | 记忆设置页、工作台界面、API 路由 | `api/routers/memory.py` | 没有 Web;设置用配置文件或命令参数 |

### 3.9 补读后新增与修正(2026-11-22)

**最重要的修正:L2 整合的输入不是 L1 事件流,而是"快照实体"。** `run_update` 读的是 `snap.read_snapshot(surface)`——工作区里现有的实体(聊天会话全文、测验作答、知识库列表)——按 `(ts, id)` 排序,再用 `*.meta.json` 里已见 id 集合做差,只处理"新实体"(`modes/update.py:150-157`)。L1 事件流(`trace`)只用在三处:偏好的脚注引用(M38)、知识库查询回顾(M44)、概览里的"积压"计数(M41)。所以 M16–M19 的快照层是整合的真正前提,不是可选附件。

| ID | 上游行为 | 位置 | 类 | 插件形态(提案) |
|---|---|---|---|---|
| M51 | `meta.json`:L2 记已见实体引用集合(`<surface>:<id>`),L3 记每个 surface 已见 L2 条目 id 集合;原子写(临时文件 + `os.replace` + `fsync`);缺失或损坏读成空并记警告;没有新输入时也刷新 `last_update_at`("检查过了,没有新内容") | `meta.py:55-182`、`modes/update.py:169-173,402-403` | D | 保持 |
| M52 | L2 update 流程:读快照 → 排序 → 差集得新实体 → 没有新实体就只刷新 meta 并(按设置)做 merge → 有则渲染、分块、逐块调用 LLM、校验 refs、追加、每块写一次检查点,最后保存 meta | `modes/update.py:140-357` | L+D | 判断由 Claude 做,其余脚本做 |
| M53 | 渲染新实体为一段时间线:每个实体 `=== @entity <surface>:<id> ===`、`ref:`、`label:`、`ts:`、`meta:`(过滤空值)、空行、正文;标记行同时是人读分隔符和机器锚点 | `references.py:166-200,302-310` | D | 保持,黄金向量 |
| M54 | 每块的"可引用池":标记出现在该块字符区间内的实体(含与块区间重叠的长实体);块前加 `# Chunk-local citeable refs` 清单 | `references.py:44-82,359-368`、`modes/update.py:675-679` | D | 保持 |
| M55 | `validate_fact_refs`:没有 refs 且要求必填则拒绝;`drop_invalid` 时池外 ref 被剔除,剔光了(且要求必填)则拒绝;不剔除时任何池外 ref 都拒绝整条;结果去重保序 | `references.py:125-163` | D | 保持,黄金向量(对照 `test_references.py`) |
| M56 | 对 LLM 常见的 ref 写错容错:去掉两端包装符(反引号、方括号、圆括号、花括号、尖括号、`^`),并接受"以允许 ref 为后缀、前缀以 `: ？ ? # / | 空白 ^` 结尾"的写法(如 `标题:chat:id`),不接受字母数字紧邻 | `references.py:313-345` | D | 保持 |
| M57 | `_parse_facts`:容忍代码围栏,取第一个 JSON 对象;只认 `{"facts": [...]}`;没有 `text` 的条目丢弃;解析失败返回空列表 | `modes/update.py:592-633` | D | 保持,换成"校验 Claude 提交的 JSON" |
| M58 | 追加事实:小节为空或不在允许清单内时,落到允许清单的第一个小节(清单为空时用 `Notes`);每条经 `AddOp` 应用,失败的跳过并记警告 | `modes/update.py:636-660` | D | 保持 |
| M59 | L3 update:对每个 surface 取"自上次以来新的 L2 条目",按 id(ULID)升序;渲染成 `### surface: <名>` + `- [小节] 文本`;可引用池是与该块区间重叠的 surface 块;事实的 refs 是裸 surface 名 | `modes/update.py:376-390,472-477`、`references.py:85-122,203-226` | L+D | 保持 |
| M60 | L3 update 与 L2 update 共用同一套校验、追加、检查点、更新后去重与合并 | `modes/update.py:495-577` | D | 保持 |
| M61 | 更新结束后:有新增事实且设置允许则自动 dedup;设置允许则自动 merge | `modes/update.py:325-348,555-577` | D | **有意偏离**:改为结束时提示用户是否去重、合并(与 M31 一致) |
| M62 | 审计(L2):把文档渲染成带行号、去掉脚注的视图;对每条记录附上它引用的**完整原始来源**(找不到就写"实体已不存在"/"无来源");对注释后的文本分块;每块一次 LLM 调用,得到行编辑,统一按**原始视图**的条目 id 锚定,允许部分成功 | `modes/audit.py:127-258`、`references.py:232-266` | L+D | 保持 |
| M63 | 行编辑语义:`replace` 要求目标行是条目行、新文本非空、refs 非空;`delete` 区间内没有任何条目则拒绝,删除区间内的条目行;`insert` 要求文本与 refs 非空、能确定小节,插在锚点条目之后(锚点不在同小节则追加到末尾);按行号**倒序**应用,单条被拒不影响其他;最后丢掉空小节 | `line_doc.py:172-298,404-437` | D | 保持,黄金向量(对照 `test_line_doc.py`) |
| M64 | `parse_edits_payload` 宽容解析:接受 `{"edits": [...]}` 或顶层数组;去围栏;用 `raw_decode` 取第一个完整 JSON;未知 `op` 丢弃;`refs` 去包装符,并且 L2 文档里形如 `m_<ULID>` 的 ref 视为幻觉丢弃(L3 不过滤) | `line_doc.py:304-398,450-471` | D | 保持 |
| M65 | dedup:最多 N 轮,每轮对全文行视图调用一次;零编辑就提前结束("迭代数是上限,不是配额");提示词只允许 replace/delete,但**代码并不强制**,模型若返回 insert 也会被应用 | `modes/dedup.py:1-16,114-169`、`prompts/en/dedup.yaml:19-20` | L+D | 保持提示词;校验里加一条"dedup 拒绝 insert"属于更严格的有意偏离,待你定 |
| M66 | merge(无 LLM):重新序列化,合并重复脚注;只有当文件字节与 `serialize` 结果不同时才重写(幂等);L3 的旧式 `m_<ULID>` ref 迁移成所属 L2 的 surface 名,找不到的丢弃 | `modes/merge.py:62-205` | D | 保持;L3 旧 ref 迁移不需要(无旧数据) |
| M67 | 快照状态:`snapshot/<surface>/state.json`(`fingerprints`、`labels`、`last_refresh`)与 `changes.jsonl`(追加);状态原子写;缺失或损坏读成空状态;变更历史读取时坏行跳过 | `snapshot/store.py:27-113` | D | 保持 |
| M68 | 快照差异:`added`(当前有、之前没有)、`removed`(之前有、当前没有,用旧标签)、`modified`(指纹变了);各自按 id 排序,依次拼接 | `snapshot/diff.py:15-67` | D | 保持,黄金向量 |
| M69 | `Entity` = `id`、`label`、`ts`、`content`、`metadata`、`fingerprint`;`EntityStamp` 是无内容的版本;探针必须与完整适配器用**同样的表达式**生成 `id`/`label`/`fingerprint`,否则"探针刷新"与"全量刷新"会对"什么变了"得出不同结论 | `snapshot/entity.py:19-73`、`adapters.py:583-600` | D | 保持 |
| M70 | 指纹:对各部分按顺序取 `sha1`(部分之间插入 `\0`,`None` 跳过,字典和列表按排序后的 JSON 序列化),取前 16 位十六进制 | `adapters.py:38-49` | D | 保持,黄金向量 |
| M71 | `chat` 实体 = 一个会话:`id` 是会话 id(其他工作区带 `ws:<id>:` 前缀),`label` 是标题,`content` 是各条消息 `### 角色\n正文`(空消息跳过),指纹 = `sha1(最后一条消息 id, updated_at)` | `adapters.py:460-515` | D | **需要重新设计**:见第 4 节第 5 点 |
| M72 | `quiz` 实体 = 一次测验作答:`id` = `<会话id>:<题目id>`,`label` = 题干前 80 字,`content` = Question / User answer / Correct answer / Explanation,指纹 = `sha1(题干, 用户答案, 正确答案, 是否正确)`,元数据含题型、难度、是否正确、是否收藏 | `adapters.py:518-577` | D | 对应本插件的账本作答 + 题库,可做 |
| M73 | `kb` 实体 = 一个已登记知识库:`id`/`label` = 库名,`content` = `# 库名`、描述、`Index versions: n`,指纹 = `sha1(描述, 排序后的索引签名)`,`ts` = **最早**索引创建时间;知识库的"查询"不在快照里,在 L1 事件流里 | `adapters.py:400-457` | D | 对应本插件的 `kb.py list/status`;"查询事件"要由 `kb.py search` 发 |
| M74 | `read_entities` 适配器抛异常时返回空列表(只记警告);`read_stamps` 在探针抛异常时回退到完整读取,"而不是报告空 surface——否则差异会把它读成'全部被删除'" | `adapters.py:658-687` | D | 保持这个契约(**空读取不能被当成全部删除**,黄金向量要覆盖) |

**上游里看到的不一致(待核实,不当依据)**
- `references.py:203-213` 的文档字符串说 L3 的输入不带标记、"L3 事实没有 refs",但 `update_l3` 提示词要求事实带裸 surface 名 refs(`prompts/en/update_l3.yaml:24-28`),`refs_in_span_l3` 也是靠 `### surface:` 标题推导池(`references.py:98-122`)。以代码和提示词为准,文档字符串过期。
- `dedup` 的"只允许 replace/delete"是提示词约束,代码层没有强制(M65)。

## 4. 与现有插件的重叠与冲突(需要你决定)

1. **0.4.0 教学档案与 `write_memory` 偏好重叠。** 上游偏好是 L3 `preferences.md` 里的一小节(M38–M40);我们 0.4.0 的教学偏好是按知识库+学习者的 JSON,并且每条必须带逐字原话、观察绑定知识点。两者不是同一个东西,但"偏好"这个概念会出现两份。提案:保留教学档案作为"有意偏离"(原话要求、知识点绑定、可真删),新增的 L3 偏好沿用上游行为;`learn` 同时读两处。是否合并,等你定。
2. **surface 的映射。** 上游 7 个 surface 都是它自己的功能区。插件里目前只有 `kb`(知识库检索)、`quiz`(测验与账本)两个有真实数据的对应物,`learn` 相当于 `chat` 的一部分;`notebook`、`book`、`partner`、`cowriter` 要等对应功能复刻之后才有数据。提案:0.5.0 只启用 `chat`(learn)、`quiz`、`kb` 三个 surface,其余保留名字但空着。
3. **M13"追加永不抛异常"** 与项目的"写入严格、读取宽容"不完全一致,但它保护的是"记录失败不能打断学习",我倾向保持上游行为,并在失败时让命令返回警告。
4. **"自动整合"改为"用户显式命令"** 已定(上一轮你选了)。这是一处必须在清单里标注的有意偏离:上游 `auto_after_update` 等(`settings.py:34-45`)在插件里变成"整合结束后提示是否去重/合并"。
5. **`chat` surface 在插件里对应什么?(补读后新增,必须先定)** 上游 L2 整合的输入是"会话全文"(M71)。插件里 Claude Code 的对话历史不在项目目录内,插件没有可读取的会话库,而全局规则又禁止我去读项目以外的文件夹。可选的实体来源:(A)`learn` 技能在每轮结束前把本轮摘要作为一个"会话实体"写入 `.tutor/memory/`;(B)只用已有的结构化记录——账本里的 `learn`/费曼作答、`kb.py search` 的查询事件、教学档案;(C)由你给出 Claude Code 会话记录的路径,允许读取。倾向 A+B,理由是都在项目内、可复算、不依赖外部文件。

## 5. 复刻顺序建议(记忆系统内部)

1. 纯函数库,先做,黄金向量对照上游测试:`ids`、`document`、`ops`、`guards`(禁用措辞)、`chunker`、`recall`。
2. L1:追加与读取命令、快照适配(只做 `kb`、`quiz`、`chat` 三个 surface)。
3. L2/L3 读取与手动编辑命令,概览,偏好写入(M35–M45)。
4. 整合:先读 `update.py`/`audit.py`/`dedup.py`/`line_doc.py`/`references.py`/`meta.py` 的实现并补全清单(M20–M33 里标"只读了头部"的),再做技能 `/tutor:memory`。

## 6. 已确认的决定(2026-11-22)

| 问题 | 决定 | 影响的条目 |
|---|---|---|
| 教学档案与 L3 偏好 | **并存**:教学档案作为有意偏离保留(原话、知识点绑定、可真删);L3 偏好沿用上游行为;`learn` 讲解前两处都读 | M35、M38–M40;`skills/learn` |
| 0.5.0 启用的 surface | **只启用 `chat`、`quiz`、`kb`**;其余 4 个(`notebook`、`book`、`partner`、`cowriter`)保留名字但不产生数据,等对应功能复刻后再启用 | M16–M19、M24、M41 |
| L1 追加失败 | **保持上游**:不报错,命令返回警告;技能必须把警告展示给用户,不能悄悄丢 | M13 |
| 分块字符预算 | **先沿用上游默认值**(1000–64000);以后按真实会话再调 | M22、M31 |

| `chat` 的数据来源(补读后新增) | **A+B**:`learn` 每轮结束前把本轮摘要写成"会话实体"存进 `.tutor/memory/`,同时用账本里的 learn/费曼作答、`kb` 查询事件、教学档案作为输入;不读项目外的 Claude Code 会话记录。代价:只覆盖通过插件技能发生的对话 | M71、M52、M59 |

这四条与第 4 节第 4 点(整合由用户显式命令触发)一起,构成 0.5.0 的范围边界。

## 7. 归属版本与有意偏离(0.5.0 实现后,2026-11-22)

拆分决定:0.5.0 只做数据层,整合排到 0.6.0。下表按 M 编号标注归属。"实现于"指代码与测试所在。

| 条目 | 归属 | 实现于 | 说明 |
|---|---|---|---|
| M1–M3 | 0.5.0 | `scripts/memory_doc.py` | 黄金向量对照上游 |
| M4、M5、M7 | 0.5.0 | `scripts/memory_doc.py` | 黄金向量对照上游 |
| M6 | **不做** | — | 旧脚注格式:插件没有旧数据;需要时只改解析器 |
| M8–M11 | 0.5.0 | `scripts/memory_doc.py` | 黄金向量对照上游;`ops`/`entry-delete` 命令 |
| M12–M15 | 0.5.0 | `scripts/memory.py` | `emit`、`trace`;M13 保持上游(追加失败不打断),命令返回 `warnings` |
| M16–M19 | 0.5.0 | `scripts/memory_snap.py`、`scripts/memory.py` | 只启用 `chat`/`quiz`/`kb`;`snapshot refresh/preview/changes` |
| M20–M33、M51–M66 | 0.6.0 | — | 整合 |
| M34 | 0.5.0(随整合一起生效) | — | 本版没有整合,`preferences` 槽位本就不会被改写 |
| M35、M41 | 0.5.0 | `scripts/memory.py` | `read-l3`、`overview`;空提示文案改为"尚无记忆" |
| M36、M37 | 0.5.0(换形态) | `skills/learn/SKILL.md` | 概览看一眼,L3 有内容且是讲解类请求才读 |
| M38–M40、M42、M43(部分)、M45 | 0.5.0 | `scripts/memory.py`、`skills/memory/SKILL.md` | `preference`、`doc-put`、`ops`、`entry-delete`;预览→应用的两步在本版是"先 `doc-get` 展示、确认后再写" |
| M44 | 0.5.0 | `scripts/memory_snap.py`、`scripts/memory.py` | `recent`、`recent-queries` |
| M46–M50 | 不做 | — | 见 3.8 |
| M67–M70 | 0.5.0 | `scripts/memory_snap.py`、`scripts/memory.py` | 状态文件、差异、实体与指纹 |
| M71 | 0.5.0(换形态) | `scripts/memory.py chat-put` | 一次 `learn` 调用一个实体,由技能提供结构化载荷,不读聊天数据库 |
| M72 | 0.5.0 | `scripts/memory.py` | 账本作答加题库,只含已作答的题;id 是账本事件 id |
| M73 | 0.5.0(换形态) | `scripts/memory.py` | 实体来自 `index.sqlite`;无描述;查询事件由 `learn` 通过 `emit` 发出 |
| M74 | 0.5.0(更严) | `scripts/memory.py` | 见下 |

**有意偏离(0.5.0 新增)**

1. **空读取守卫(M74、M17)。** 适配器读取失败,或读到零个实体而保存的状态非空,刷新不写任何文件,需要 `--allow-empty` 才记为全部移除;账本有损坏行时是"降级读取",不记任何 `removed`。上游读取失败会返回空列表并被记成全部删除(`adapters.py:658-666`)。
2. **无变化的刷新不写文件。** 上游每次刷新都重写 `last_refresh`;本版没有差异就什么都不写,`last_refresh` 停在上一次真正的变化。0.6.0 整合如需该字段再改。
3. **按学习者隔离。** 上游按账号隔离;插件按 `--learner` 隔离,目录 `.tutor/memory/<学习者>/`。
4. **`chat` 与 `kb` 的来源换形态(M71、M73)。** 见上表。`kb` 查询事件(M73)由 `learn` 在学习者提问时发出;`kb.py search` 本身不写 L1。
5. **不读旧格式(M6)。**
6. **`learn` 的 L3 读取时机(M36、M37)。** 上游靠挂载工具与用户选择;插件由技能判断"讲解类请求且 L3 有内容"。
7. **偏好文本去重键(M39)。** 沿用上游:按空白拆分后用单空格连接再转小写;中文字之间的空格仍算不同。
