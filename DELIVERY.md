# 嵌套子管道状态传播收敛为处理管道公开方法 — 交付文档

## 1. 现状与问题

嵌套转换把一段子管道挂在条件下执行，子管道的处理状态需要传回外层管道。改动前该职责
散落在两个实现里，各自手写且口径不一致：

| 位置 | 手写搬运 | 覆盖容器 |
|------|---------|---------|
| `NestedProcessingTransformation.apply`（`sigma/processing/transformations/meta.py:51`） | 5 行逐容器赋值 | applied / applied_ids / field_name_applied_ids / field_mappings / state |
| `NestedQueryPostprocessingTransformation.apply`（`sigma/processing/postprocessing.py:186`） | 1 行 | 仅 applied_ids |

同一职责两份实现：预处理侧五个容器各用各的合并语义（`extend` / `set.update` /
`dict.update` / `FieldMappingTracking.merge` / `dict.update`），查询后处理侧只搬运
`applied_ids`。新增容器或调整语义时必须逐处同步，且两处都直接读写下层管道属性。

## 2. 方案与设计取舍

### 2.1 公开方法形态

```python
ProcessingPipeline.absorb_nested_state(self, nested: "ProcessingPipeline") -> None
```

- **入参是“一个子管道”而不是“五个容器”**。调用方不需要知道有哪些容器、各自怎么合并；
  新增容器时只改这个方法。这也正对应题面“新增状态容器或调整语义时要逐处同步极易漏改”的
  痛点：收敛后只有一处。
- **方向由外向内（`outer.absorb_nested_state(inner)`），与现状调用点一致**。两个嵌套转换
  都持有外层 `self._pipeline` 和子管道 `self._nested_pipeline`，调用形态是
  `self._pipeline.absorb_nested_state(self._nested_pipeline)`，与原来的手写搬运同形，
  改动最小、语义最直白。
- **不接收规则/查询返回值**。方法只负责簿记传播；子管道的驱动（`apply` /
  `postprocess_query`）和返回值仍由转换自己完成，保持它们的既有行为（预处理侧返回 None，
  后处理侧返回新查询）。
- **返回 `None`**。传播是副作用，没有可用的返回值；这样也避免调用方误以为可以链式拿状态。
- **容器清单写死在方法体，不做成参数**。“哪些容器算处理状态”是处理管道自身的契约，不应
  由每个调用点各自声明——否则又回到“各处手写、口径漂移”。清单以 docstring 逐条列出。
- **不做“是否已驱动”的前置校验**。现有调用点都是在子管道驱动完之后立即传播；额外加标记
  位会改动处理管道状态容器定义（题面要求保持改动面收在处理管道与嵌套转换及其测试，且
  不加新容器），收益不抵风险。方法用类型检查兜底明显误用（传非管道对象）。

### 2.2 逐容器的合并语义（与现状逐项一致）

| 容器 | 本方法 | 改动前预处理侧 | 改动前后处理侧 |
|------|--------|--------------|--------------|
| `applied` | `extend` | `extend` | 不搬运 |
| `applied_ids` | `set.update`（并集） | `set.update` | `set.update` |
| `field_name_applied_ids` | `dict.update`（同键覆盖） | `dict.update` | 不搬运 |
| `field_mappings` | `FieldMappingTracking.merge()`（深合并、链式解析） | 同 | 不搬运 |
| `state` | `dict.update`（同键覆盖） | 同 | 不搬运 |

查询后处理侧由“只并 `applied_ids`”改为“走统一方法”。因为查询后处理的子管道只跑
`postprocessing_items`，`apply()` 不会被调用，所以它的 `applied` / `field_name_applied_ids`
/ `field_mappings` / `state` 天然为空——统一方法在后处理路径上的实际读数与旧的单容器搬运
完全一致（见第 5 节路径 2 的逐容器键数），没有行为漂移，只是把“四个空容器 + 一个并集”的
隐式事实显式化为统一路径。

### 2.3 被否决的备选

- **把方法做成 `@staticmethod` 接收两管道**：接口更宽，但丢失了“外层管道吸收子管道状态”
  的意图表达，且与既有 `postprocess_query` / `track_field_processing_items` 等实例方法风格不符。
- **让子管道反推外层（`inner.propagate_to(outer)`）**：子管道并不持有外层引用，需要在
  构造时反向注入，会改动 `ProcessingPipeline.__post_init__` 与 `__add__` 的绑定语义，
  超出“改动面收在处理管道与嵌套转换”的约束。
- **只登记容器名、在转换里 for 循环**：这等于把合并语义又散回调用点，只是换了个写法，
  未解决“新增容器要逐处同步”的根因。

## 3. 题面要求 → 改动落点（逐条）

| 题面要求 | 落点 |
|---------|------|
| 嵌套子管道的状态传播收敛为处理管道的公开方法 | `sigma/processing/pipeline.py:984` `ProcessingPipeline.absorb_nested_state()` |
| 预处理侧嵌套转换统一改调该方法 | `sigma/processing/transformations/meta.py:56` |
| 查询后处理侧嵌套转换统一改调该方法 | `sigma/processing/postprocessing.py:190` |
| 两处不直接触碰管道内部状态容器 | 两处 `apply` 均无 `_pipeline.<容器>` 读写；由测试 `..._does_not_touch_outer_state_containers` 用“访问即报错的哨兵容器”实测守卫 |
| 外层吸收后的状态语义与现状逐项一致 | 第 2.2 节表；验收读数 before/after 除新增调用计数外逐行相同（第 5 节） |
| 字段映射台账的深合并不回归 | `field_mappings` 仍走 `FieldMappingTracking.merge()`；用例 `test_processingpipeline_absorb_nested_state_merges_five_containers`、`test_nested_pipeline_transformation_absorbs_all_five_containers` 断言链式映射 `fieldA→mappedA→finalA` 的台账与 `target_fields` 反查 |
| 处理标识登记不回归 | `applied_ids` 并集；用例 `test_nested_query_postprocessing_transformation_absorbs_applied_ids`、`test_processingpipeline_absorb_nested_state_merges_five_containers` |
| 不经嵌套直接使用子管道的簿记位置不变 | 方法只读子管道、不清空；用例 `test_processingpipeline_absorb_nested_state_keeps_nested_bookkeeping`、`test_processingpipeline_direct_nested_use_keeps_bookkeeping_local`；验收路径 3 |
| 公开方法的形态与边界自行设计并写明理由 | 第 2.1 / 2.3 节 |
| 测试面在三个既有测试文件内扩展三类断言用例 | 第 4 节测试登记 |
| 不改动既有断言形态 | 既有用例零改动（`git diff` 仅新增节点）；详见第 4 节 |
| 改动面收在处理管道与两个嵌套转换实现及其测试 | 产品改动 3 个文件，测试 3 个文件；另有一份文档章节与独立验收脚手架（不参与产品导入） |
| 既有测试保持通过 | 第 5.1 节 |
| 验收以指定 pytest 命令零失败为准 | 第 5.1 节实测 `363 passed` |
| 构造读数口径：一条同时携带链式字段映射与处理标识的最小治理事件，经三条路径各驱动一遍 | `acceptance/nested_state_acceptance.py`：`EventID→event.id→event.code` 链式映射 + `map_event_id`/`map_event_code`/`set_index` 标识 |
| 读数采五个容器逐容器键数与逐键内容、直接使用路径簿记留痕条数、两处嵌套转换调用公开方法次数 | 脚本输出 `keys=` / `content=`、`ledger_trail_entries=`、`*_calls=` |
| 全部读数在快照环境内零外部依赖可复现 | 脚本仅用标准库 + 本仓库 `sigma` 包；`python -I` 隔离模式实测 |

## 4. 改动文件登记

### 产品代码

| 文件 | 性质 | 改动内容 |
|------|------|---------|
| `sigma/processing/pipeline.py` | 新增公开方法 | 在 `postprocess_query()` 之后新增 `absorb_nested_state()`（+33 行，含 docstring 逐容器语义说明与类型兜底） |
| `sigma/processing/transformations/meta.py` | 收敛调用 | `NestedProcessingTransformation.apply` 的 5 行逐容器赋值替换为 1 行 `absorb_nested_state()` 调用（−5/+1） |
| `sigma/processing/postprocessing.py` | 收敛调用 | `NestedQueryPostprocessingTransformation.apply` 的 `applied_ids` 单容器搬运替换为 `absorb_nested_state()` 调用（−1/+1） |

### 测试

| 文件 | 新增节点数 | 节点 |
|------|-----------|------|
| `tests/test_processing_pipeline.py` | 5 | `test_processingpipeline_absorb_nested_state_merges_five_containers`、`..._keeps_nested_bookkeeping`、`test_processingpipeline_direct_nested_use_keeps_bookkeeping_local`、`..._rejects_non_pipeline`、`..._does_not_need_container_access` |
| `tests/test_processing_transformations.py` | 3 | `test_nested_pipeline_transformation_uses_public_absorb_nested_state`、`..._does_not_touch_outer_state_containers`、`..._absorbs_all_five_containers` |
| `tests/test_postprocessing_transformations.py` | 3 | `test_nested_query_postprocessing_transformation_uses_public_absorb_nested_state`、`..._does_not_touch_outer_state_containers`、`..._absorbs_applied_ids` |

三类断言覆盖：

1. **预处理侧嵌套转换改调公开方法**：`uses_public_absorb_nested_state`（monkeypatch 计数，
   断言恰好 1 次、外层/子管道对象正确）、`absorbs_all_five_containers`（五容器逐项断言）。
2. **查询后处理侧嵌套转换改调公开方法**：`uses_public_absorb_nested_state`、
   `absorbs_applied_ids`。
3. **两处均不直取管道内部状态容器**：`does_not_touch_outer_state_containers`（两文件各一）。
   做法是把外层管道五个容器替换为“任何属性访问/布尔/迭代都抛 AssertionError”的哨兵对象，
   并把公开方法 stub 掉；转换若绕开方法直取容器会立刻失败。已做变异验证：把
   `meta.py` 临时还原为旧的 5 行手写搬运，该用例即报
   `AssertionError: ... accessed pipeline state container 'applied' directly`（验证后还原）。
   `does_not_need_container_access` 作为辅助守卫，断言容器清单常量与两个 `apply` 源码中
   不存在 `*_pipeline.<容器>` 形式。

既有断言形态未改动：`git diff tests/` 仅新增节点与两处 import 扩展（补充
`NestedProcessingTransformation` / `NestedQueryPostprocessingTransformation` /
`_guard_pipeline_state_containers`），无既有用例修改。

### 文档与验收脚手架（非产品代码，不被 `sigma` 包导入）

| 文件 | 性质 | 内容 |
|------|------|------|
| `docs/guides/processing_pipelines.rst` | 用户文档 | 新增 “Nested State Propagation” 一节：方法签名、五容器合并语义、直接使用不经嵌套的簿记位置说明（按仓库 `.github/instructions/docs.md` 要求同步公开 API） |
| `acceptance/nested_state_acceptance.py` | 验收脚手架（新增） | 零依赖读数脚本，支持 `PYSIGMA_REPO_ROOT` 指向另一快照以采集对照读数 |
| `acceptance/readings_before.txt` | 验收记录（新增） | 改动前基线（HEAD 快照）实测读数 |
| `acceptance/readings_after.txt` | 验收记录（新增） | 改动后实测读数 |

## 5. 验收实测读数

环境：Windows 11，CPython 3.12.9，仓库快照 HEAD `65a27f7`。为运行测试需
`pip install google-re2`（`pyproject.toml` 已声明该依赖，属环境补齐，非本次改动）。

### 5.1 题面指定验收命令

```
$ python -m pytest tests/test_processing_transformations.py \
      tests/test_postprocessing_transformations.py tests/test_processing_pipeline.py -q
363 passed in 0.98s
```

改动前同命令为 `352 passed`；新增 11 个节点，零失败。全量 `python -m pytest tests -q`
为 `1956 passed, 2 skipped, 1 error`，其中 1 error 是 `tests/files/template_vars.py` 被
当作测试模块收集导致的既有问题（基线上同样存在：基线为
`1 failed, 1944 passed, 2 skipped, 1 error`，那 1 failed 是 `test_validators_tags.py`
依赖网络的既有失败），与本次改动无关。`python -m mypy -p sigma` 输出
`Success: no issues found in 79 source files`。

### 5.2 构造读数

固定输入（`acceptance/nested_state_acceptance.py` 内 `MINIMAL_EVENT` + 子管道）：

```
title: GovernanceEvent
logsource: {category: test}
detection: {sel: [{EventID: 1, User: alice}], condition: sel}
fields: [EventID, User]
子管道 items:
  map_event_id   : FieldMappingTransformation({EventID: event.id})
  map_event_code : FieldMappingTransformation({event.id: event.code})
  set_index      : SetStateTransformation(index, governance-index)
```

即一条同时携带链式字段映射（`EventID→event.id→event.code`）与处理标识的最小治理事件。

**路径 1 — 预处理侧嵌套转换**（`outer.apply(rule)` 后外层读数）：

```
applied:                keys=4 content=[True, True, True, True]
applied_ids:            keys=4 content=['map_event_code', 'map_event_id', 'outer_nest', 'set_index']
field_name_applied_ids: keys=1 content={'event.code': ['map_event_code', 'map_event_id']}
field_mappings:         keys=2 content={'EventID': ['event.code'], 'event.id': ['event.code']}
state:                  keys=1 content={'index': 'governance-index'}
rule.fields = ['event.code', 'User']
```

**路径 2 — 查询后处理侧嵌套转换**（`outer.postprocess_query(rule, 'User="foobar"')`）：

```
postprocessed query = '[User="barbar"]'
applied:                keys=0 content=[]
applied_ids:            keys=3 content=['outer_nest', 'replace_foo', 'wrap_query']
field_name_applied_ids: keys=0 content={}
field_mappings:         keys=0 content={}
state:                  keys=0 content={}
```

**路径 3 — 不经嵌套直接使用子管道**（`direct.apply(rule)`）：

```
applied:                keys=3 content=[True, True, True]
applied_ids:            keys=3 content=['map_event_code', 'map_event_id', 'set_index']
field_name_applied_ids: keys=1 content={'event.code': ['map_event_code', 'map_event_id']}
field_mappings:         keys=2 content={'EventID': ['event.code'], 'event.id': ['event.code']}
state:                  keys=1 content={'index': 'governance-index'}
ledger_trail_entries = 2
```

**公开方法调用次数**（计数器包裹 `ProcessingPipeline.absorb_nested_state`）：

```
preprocessing_transform_calls=1
postprocessing_transform_calls=1
direct_path_calls=0
```

### 5.3 改动前后逐项比对

同一脚本以 `PYSIGMA_REPO_ROOT` 指向 HEAD 快照（`git worktree` 临时检出，采集后移除）
得到 `acceptance/readings_before.txt`；`diff readings_before.txt readings_after.txt` 除
新增的三行调用计数外逐行相同，即：

- 路径 1 五容器逐容器键数与逐键内容：完全一致；
- 路径 2 五容器读数：完全一致（旧实现只并 `applied_ids`，其余四容器为空；统一方法结果相同）；
- 路径 3 簿记留痕：位置与条数（`ledger_trail_entries=2`）完全一致；
- 唯一新增读数为调用次数，改动前公开方法不存在，基线脚本该三项输出
  `n/a (public method absent)`。

## 6. 复现方式

```bash
# 1) 题面指定验收
python -m pytest tests/test_processing_transformations.py \
    tests/test_postprocessing_transformations.py tests/test_processing_pipeline.py -q

# 2) 构造读数（零外部依赖，隔离模式）
python -I acceptance/nested_state_acceptance.py

# 3) 与基线快照对照
git worktree add ../.baseline-t289 65a27f7
PYSIGMA_REPO_ROOT=../.baseline-t289 python -I acceptance/nested_state_acceptance.py
git worktree remove ../.baseline-t289 --force
```
