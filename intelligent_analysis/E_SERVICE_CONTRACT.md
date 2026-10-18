# E 服务契约 0.6.2（2026-10-18）

0.6.2 自检修复：数值成功结果拒绝非有限数、负值、布尔值、非数值内容、非列表/空白来源以及无效 ISO 时间。合法零值、小数、数值字符串保持原样；违约抛 DependencyUnavailable。P2PNet、知识产物格式及 D/C 调用签名不变。端到端任务和自检见 [完整工作流](docs/E全流程任务与自检-2026-10-18.md)。

补充接收：C 0.2.1a2 / 0007 已通过 E 隔离验收，调用边界不变；见 [a2接收回执](docs/致C-021a2接收回执-2026-10-17.md)。该段为0.6.1接收背景；当前运行版本为0.6.2，旧发布原件保留。

0.6.1 修正知识原文/索引版本耦合，视频入口新增 D 指定 analysis_id，数值工具不完整返回映射为 DependencyUnavailable。已接收 C 0.2.1a1 / 0007；新视频结果由 C uow.visible 统一存储。完整变更与接收边界见 [0.6.1 回执](docs/E061-接收C021与答复D-2026-10-17.md)。

正式视频算法改为P2PNet，停止模型对照。count_model=p2pnet、confidence_threshold=0.5；D/B必须同步适配。知识产物格式、解析器及关键词算法版本继承0.5.1，C/MySQL正式任务联调仍按原执行单推进。见 [0.6.0交付与D/B执行单](docs/E060-P2PNet正式交付与DB执行单.md)。

统一入口保持 `intelligent_analysis.service`。本版包含破坏性视频契约变更，D 必须同时注入队列与读回能力；仅修改模型字段不能自动完成联调。

## 1. 职责与绑定

D 唯一 Worker 领取、续租、恢复和分发；D 通过 C 持久化计算结果、失败、进度及作业状态；E 只计算。D 负责授权、上传、回放 Range、权限、CSRF、HTTP 与序列化。E 不运行独立的队列消费者。

```python
from intelligent_analysis import ServiceBindings, configure_services

configure_services(ServiceBindings(
    submit_job=d_submit_job,
    load_counts=d_load_visible_counts,
    validate_analysis=p2p_counter.validate_config,
    knowledge_snapshot_loader=lambda: load_verified_knowledge_snapshot(
        cstore, authorized_ready_document_ids(), data_root),
))
```

绑定必须在每个 API 进程启动时完成；只注入服务端受信任函数，不能从用户请求接受 Python 调用对象。

上例需先从 `intelligent_analysis.knowledge.artifacts` 导入 `load_verified_knowledge_snapshot`。签名为 `(store, document_ids: list[str], data_root: str | Path) -> dict`；D 负责构造真实 C store、过滤有权访问且 ready 的文档、在 API/Worker 启动绑定。E 的 answer_message 每次调用该绑定后构建检索索引；from_payload 不是数据库校验器，不能用宽松快照替代严格 loader，也不在 E 内部另建数据库连接。D 现有严格绑定符合约定。

| 绑定 | 签名和责任 |
|---|---|
| `submit_job` | `(kind: str, payload: dict) -> dict`；D 登记/去重并提交事务之后返回 `status=queued, job_id`，视频另有 `analysis_id`。这些 ID 必须指向真实 C 记录。 |
| `load_counts` | `(video_id: str, analysis_id: str) -> dict`；校验视频、分析和访问归属，读取双方确认的新口径存储；返回 `CountsResponse`，含两种粒度，E 负责过滤。未知对象由 D 报 404/403；旧 `entries/exits` 禁止作为新响应。 |
| `validate_analysis` | `(config: dict) -> None`；真实检查权重文件摘要、可加载性、模型版本和运行设备，不可用时抛 `UnsupportedAnalysis`。 |
| `knowledge_index_path` | D 提交成功后发布的完整活动索引 JSON。问答每次读取完整文件，原子替换确保不同 API 进程读到同一已发布快照。 |
| `knowledge_snapshot_loader` | 0.5.1 可选绑定 `() -> dict`，由 D 列举有权访问的活动文档 ID，再调用 `load_verified_knowledge_snapshot(store, ids, data_root)`；每次问答复验并加载。与 knowledge_index_path 互斥，任何产物损坏都返回不可用，不沿用旧内存。 |

异常映射：`DependencyUnavailable` → 503；`UnsupportedAnalysis` → 422 unsupported；配置校验异常由 D 映射其现有 400/422 约定。E 返回的 `analysis_not_ready` → 409；`data_insufficient`、`quality_insufficient`、`insufficient_evidence` → 200；正常 `metric_tool_unavailable` 在当前 D/19 HTTP 为 200 业务不足状态，空引用；上游结果违约抛 `DependencyUnavailable` 才映射 503。日志错误摘要由 D 脱敏。

## 2. 视频三函数

```python
analyze_video(video_id: UUID, config: dict, *, analysis_id: UUID | None = None) -> dict
get_camera_counts(video_id: UUID, analysis_id: UUID,
                  start: str | None = None, end: str | None = None,
                  grain: str = "5min") -> dict
forecast_short_term(video_id: UUID, analysis_id: UUID, cutoff_at: str,
                    horizon_minutes: int, step_minutes: int = 5) -> dict
```

分析config：count_model="p2pnet"、weights_version、confidence_threshold=0.5（可省略，序列化包含默认值）、roi={points:[{x,y},...]}、sample_interval_seconds（默认10，整数1—600）、aggregation_minutes=5、inference_engine、device。当前仅pytorch/CPU通过运行验证，其他运行时由计数器拒绝。PyTorch必须省略openvino_version，多余字段拒绝；ROI为不自交、非零面积、顶点不重复的归一化多边形。

首个配置weights_version=leader-SHTechA-50604773、PyTorch CPU；权重SHA256 `506047732b128ff09efef18e94bfacbe35fcfef300e5e9eeeece259b0488c63f`。D使用`P2PNetCounter.from_team_bundle(trusted_bundle_dir)`，或显式提供WeightSpec、受信任model_factory和source_revision。前者校验收到源码文件摘要并严格加载权重；未改动源码在CUDA可用主机选CUDA锚点，所以该CPU构建器明确拒绝这种未验证环境。不得自动回退DM或把P2P标成DM。模型源码/权重现随Git仓库model_bundle/p2pnet交付，wheel保持轻量不内含模型。

分析返回 `{job_id,analysis_id,status:"queued",video_id,count_metric:"camera_visible_people",inference_engine}`。

可选 analysis_id 由 D 服务端分配并传给 submit_job；D 返回的身份必须一致。省略时由 D 分配，兼容旧调用。E 始终先执行配置/运行能力校验再 submit。API 不收 file_path，E analyze_video 也不从客户端取得路径：D 的 submit_job 在同一 C 事务登记 job 与 visible analysis；Worker 领取后使用 `store.get_visible_input(analysis_id, lease)` 取得受检文件、录制时间及 source_ids 再调用 E。现有 D 先 validate_analysis 再 submit_job 的编排有效，切回 E 入口时无需取消 D 的授权/归属检查。

计数返回 `video_id,analysis_id,metric,status,roi,analysis,points,manual_validation`。`analysis` 是实际完整 config；`manual_validation` 未人工评估时为 null。每点包含 `metric,value,unit="人",grain,quality_status,source_ids,published_at,ingested_at`。`frame` 点使用 `recorded_at`；`5min` 点使用 `period_start,period_end,sample_count,aggregation_rule="median"`。时间必须带时区，聚合窗恰好 5 分钟；查询仅返回完全落在 start/end 内的点。

E计算产物的published/ingested为空，由D/C提交时填写。P2P逐帧值为ROI内置信度严格大于0.5的点数，数据类型仍float；偶数样本的中位数可为小数，保留原值。good只表示抽样完整且数值有效，不表示人工准确率通过。缺样degraded，零有效样本unavailable/value=null/sample_count=0；不足5分钟不输出聚合值。逐帧人数不累加成人次。

预测返回 `metric,status,forecast_points,training_cutoff,input_window_minutes,model_version,validation_error,reason`；点含 `target_time,value,is_forecast=true,lower=null,upper=null`。方向参数和旧 `interval_minutes` 不再接受。只使用窗结束时间不晚于 cutoff 的 `good` 点；重复时刻拒绝，缺失/中断/过旧返回质量不足。

15/30 分钟最低训练点数分别为12/24；还需要至少3个完整滚动起点回测折，因此常数连续样本至少分别17/32个点才能满足完整验证。每折在自己的训练前缀用固定 alpha/beta/phi 网格选择一阶 SSE 最低的阻尼 Holt 参数；分跨度 MAE 不优于最近值基线时不输出曲线。60分钟一律保持不足状态，直到另行验收。没有可靠区间时保持 null。

## 3. D 的计算与发布流程

```python
from intelligent_analysis.worker import dispatch_analysis_job, publish_knowledge_snapshot

# D 已在短事务内领取并取得租约；长计算期间由 D 续租。
prepared = dispatch_analysis_job(job_type, payload, counter=p2p_counter)
# D 在新 write 事务校验当前租约、写业务数据、完成任务；同事务提交。
# 知识提交之后，由 D 在单一发布锁下重建完整活动快照：
publish_knowledge_snapshot(full_committed_snapshot, published_active_index_path)
```

视频 payload：`video_id,analysis_id,file_path,recorded_start_at,recorded_end_at,source_ids,config`。帧输入为 RGB uint8；E 读取 OpenCV 录像时执行 BGR→RGB，帧率无效时拒绝推导时间。返回完整 `CountsResponse`，不调用 C 的旧 `write_counts()`。

知识 payload：`document_id,document_version,version,file_path,metadata`。document_version 是 C 原文版本；version 是本次索引版本。也可在 metadata.document_version 提供原文版本；两处同时提供必须一致，不从索引版本推导原文版本。metadata 至少有标题、发布方、使用说明，可含 URL、发布时间、适用起止与 applicability_note。E 返回 `chunks`（C chunk_no/text/locator）、`artifact_bytes`、SHA256、标准索引 `snapshot`，并返回 document_version/index_version/parser_version/embedding_model_version；均为未发布计算结果，含 bytes 的结果不能直接作为公共 HTTP JSON。

0.5.1 正式单文档产物根为 schema_version=1、parser_version、embedding_model_version；documents[document_id] 独立记录 document_version 和 index_version。解析器与关键词算法未改变，仍使用 text-parser-0.5.0 / keyword-tfidf-0.5.0；这是关键词 JSON 索引，没有 embedding 向量。load_verified_knowledge_snapshot(store, document_ids: list[str], data_root: str | Path) 返回 {documents: {id: {document_version,index_version,metadata,chunks}}}，逐项核对 C 原文版本、活动索引版本、元数据、连续 chunk_no、文本、chunk_id/order locator、文件路径及 SHA/大小。损坏、缺失、版本或内容不一致均抛 DependencyUnavailable；重复文档 ID 属调用参数错误，抛 ValueError。旧无版本清单以及未发布草案 artifact_schema_version/version 格式须重建。该校验不证明源资料解析正确，也未关闭 C 的完整 G10。

D 在事务外用 C 原生 persist_file 落盘 artifact_bytes 后，先用短事务 begin_index，再逐批 write_chunks（每批不超过1000，分别提交）；最后单独的短 write 事务执行 activate_index+complete_job，一起提交或回滚，激活时附 expected_artifact_byte_size 和当前 lease。解析、推理、文件持久化在长事务之外，D 在阶段之间保持续租。失败与重试由 D 根据 C 真实接口处理，过期租约禁止发布。推荐注入 knowledge_snapshot_loader 直接从 C 活动引用读取；旧 publish_knowledge_snapshot 接收完整活动版本集合而非单文档增量，D 重启须从 C 重建。外层调度不得在知识处理器已原子完成后再次 complete_job。

## 4. 知识服务与数值查询

0.6.1 数值成功缺任一必填键、value为空、来源为空或结果不是对象时抛 DependencyUnavailable；不足结果不得带 value/source_ids，同样违约则拒绝。D 映射为 503，不再把上游半个成功漏为 ValueError/500。正常不足返回空引用及空 used_metric_queries，不退回知识文档补数字。

`index_document(file_path, metadata, *, index_version=None)` 要求 metadata 的 `document_id`、`document_version` 由 C 登记确定；index_version 是独立索引版本，由 D 显式提供或 E 生成唯一 `e-index-<UUID>`，payload.version 使用它，不再复制原文版本。返回 D 真实任务 ID。原文版本格式按 C 原样读取，不强制完整64位摘要；C 本批有 sha256:32位前缀标识，文件完整性另用完整 SHA256。`reindex_documents(ids, reason)` 向 D 提交 `knowledge_reindex` 操作；D 需展开为每个文档的 `knowledge_index` 任务并返回其管理作业 ID，不能把批量 payload 直接交给单文档处理器。

`process_document_index(file_path,metadata,document_id=None)` 是同步本地索引 helper，不登记任务或写 C；D 的正式异步流程使用上述 worker 产物及提交后发布。`load_knowledge_index/save_knowledge_index` 保持导出。

`answer_message(message,conversation_id=None,context=None)` 的 D 侧数值路由传入：

```python
context = {"metric_query": constrained_query_dict, "metric_tool": trusted_d_query_callable}
```

查询白名单、实体/指标/时间粒度校验由 D 实施；E 不生成 SQL。工具结果必须带 `metric,value,source_ids,period_start,period_end,queried_at`，完整保存在 metric_result；缺来源时拒绝给出数值结论。未注入工具返回 metric_tool_unavailable。无 metric_query 时走文档检索，历史/未生效材料在回答正文与 limitations 中提示，绝不将旧公告当作当前运营规定。该入口不隐式执行任意自然语言数据库查询，D 需确定数值意图并注入受约束 query。

## 5. 实验与验收

正式计数器位于vision.p2p_counter.P2PNetCounter；复用P2PNetAdapter输出归一化人头点/置信度，worker以point_count执行ROI计数。provenance提供模型名、权重版本/摘要、收到源码标识、CPU及阈值，D应留存审计。人头点、权重路径及原始字节不直接作为HTTP响应。停止DM/P2P比较，旧compare_counters、DM工具及既有对照材料只作为历史，不参与正式模型选择。

人工MAE仍需真实独立复核标签；GPU、正式D/B视频HTTP和真实预测均待验收。C021新口径持久化已完成E隔离合成接收，不等于D已安装和接通。已有DM分析应作为历史单独保留，新接口不将其改名成P2P；需要新任务/analysis_id后重新计算。
