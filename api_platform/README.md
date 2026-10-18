# api_platform —— 唯一后端与 HTTP 层（D）

## 2026-10-18：网页 P2PNet 一键计数

原录像页手填分析 UUID 的表单改为“开始计数”、抽帧间隔、进度和状态。`POST /api/v1/videos/{id}/counting` 为管理员/CSRF 写入口，自动创建/复用任务；`GET .../counting` 为登录后的状态与最近分析发现入口；既有 `GET .../counts?analysis_id=...&grain=frame|5min` 支持目录录像，编号由页面自动传递。原正式录像仍走既有 C/D 分析及任务机制。

目录录像任务/结果保存于 C 新表 `local_video_analyses`、`local_video_count_points`（迁移 `local_video_counts_04`），D 单线程后台执行器调用 E `P2PNetCounter` / `point_count`，默认全画面 ROI、每 5 秒抽帧。结果以 `time_basis=relative`、`offset_seconds` / `period_start_seconds` 返回，不制造录制日期。前端兼容 P2PNet 的 `roi.points`、帧 `recorded_at` 及相对时间，正式接口不再按 DM-Count 校验。

任务按视频摘要/参数去重，完成后原子落库，替换录像使旧计数失效；重启后状态查询可恢复超时任务，失败可重试。计算前后核验文件摘要，不填补缺测或伪造人工核验。短片不足 5 分钟不生成完整窗口；目录录像当前提供计数/趋势，正式短时预测沿用原时间契约。

API/Worker 包入口识别同级 E 源码目录，避免已有 reload 父进程不能加载后来可编辑安装的 E 包。真实 MySQL 与浏览器已验证当前录像的 29/39/44/37 四个抽样点、拖动同步和刷新恢复。详见 [网页计数说明](../P2PNet网页计数说明.md)。

本次相关后端 81 项、前端单元 42 项、录像浏览器回归 8 项通过，类型检查及构建通过。已实测网页启动每秒抽样的 18 帧计算任务及结果持久化；验证摘要在 `data/p2pnet-web-counting-verification.json`。

## 2026-10-18：接收 E 完整仓库与 P2PNet 模型

E 更新到提交 `2b6bb99336376b5f9a173b5a4d1cb1097785ae49`，包版本 0.6.2。`项目++++/intelligent_analysis/` 改为 E 仓库根目录，内部同名子目录为 Python 包；项目现有 `.venv/python.exe` 已可编辑安装该包。模型源码、`SHTechA.pth`、测试短片、配置、核验脚本与文档一并接收。

默认设置和本机 `.env` 中 `P2PNET_BUNDLE_DIR` 均指向 `../intelligent_analysis/model_bundle/p2pnet`（基于本后端目录解析）。其他数据库、大模型密钥、知识和视频目录配置保留。模型接收的 9 个成员摘要匹配，CPU 模型校验和自带短片三个抽样帧 31/41/31 的推理通过。模型绑定测试读取实际配置目录，避免继续查找旧的空 `data/p2pnet_bundle` 而跳过。

这次完成源码、模型和后端调用配置；业务录像的分析入库及前端 P2PNet 结果适配仍需另外接通，不将模型可调用等同于页面已有真实人数结果。详见 [E 本地接入说明](../intelligent_analysis/本地接入说明.md)。

E 源码全部保持上游版本。之前本机对 E HTML 解析的扩展迁到 D 的 `app/services/knowledge_input.py`：Worker 从已核验 HTML 原件提取正文到临时 Markdown 后交给 E，完成后清理临时文件，原件及来源登记保持不变。

本次后端相关 129 项测试通过；E 套件 137 通过、3 跳过。真实 D 模型绑定已对根层现有视频首帧算得估计 29 人；登录、列表、Range 播放、知识文档列表检查通过。另有 7 条问答旧断言在更新前后均失败，详见 E 本地接入说明，不计为本次回归通过。

## 2026-10-18：videos 根目录自动回放

前端恢复说明：已参照 B 上游 `eaf36e31a399bf6bb6a91fe6f81794632daf1829` 的 `VideoReplayView.vue` 恢复播放器/人数统计双列、分析关联、ROI/模型信息与趋势预测面板。取消按 `local_folder` 隐藏功能区域的处理；无分析结果时保留结构并显示空值，不能生成预测时禁用按钮。目录读取接口和数据库不变。

直接把视频放入 `项目++++/videos/` 根层。`VIDEO_STORAGE_DIR` 默认 `../videos`，通过配置解析为相对于本后端目录的绝对路径。`GET /api/v1/videos` 调用 C 的 `ingestion/local_videos.py` 扫描并增量写入 MySQL `local_video_files`；前端每 15 秒检查，也提供“刷新视频目录”。不要求上传或另行登记来源、摄像头和录制日期。未知的业务字段保持空值。

列表新增可选字段 `title`、`origin`（目录条目为 `local_folder`）、`duration_seconds`、`file_digest`、`source_note`；目录条目的 `scenic_id`、`camera_id`、`recorded_start_at`、`recorded_end_at`、`permission_record_id` 为 null。原有登记录像格式兼容。播放继续走登录保护的 `GET /api/v1/videos/{id}/playback`，校验根目录边界、文件存在和摘要，并支持 Range。

迁移 `local_video_directory_03` 新建目录索引表，本机 MySQL 已升级。数据库仅记录文件信息，文件保留在 `videos` 根层；替换更新记录、移除标记 missing。本地条目仅提供视频回放，画面人数分析需另行处理。使用方法见 [视频目录说明](../videos/README.md)。下方较早记录中“必须上传登记”“视频表为空”的描述仅对应当时版本。

本次验证：录像、配置及 CStore 相关 42 项后端测试、39 项前端单元测试通过，类型检查及生产构建通过。在本机真实 MySQL/API/页面验证根层录像播放、暂停、拖动、目录刷新及 HTTP 206 Range 读取。现有 17.16 秒测试片段原 FMP4 编码不被浏览器支持，根层已改为 H.264 版本（960×540，6,683,467 字节），原始文件保留在归档子目录；后续素材建议 MP4 / H.264。播放流在浏览器中止请求时关闭文件句柄，避免 Windows 上刷新后无法替换文件。

安徽文旅交通客流观察与预测驾驶舱的 FastAPI 应用。按团队架构文档，本目录是项目唯一的后端入口，负责全部 30 个 HTTP 路由、认证、权限、业务查询与历史周/月预测；数据访问通过 C 的 `data_foundation` 包，智能分析与录像计数通过 E 的 `intelligent_analysis` 包，本目录不直接连数据库、不直接实现模型。

> **维护约定（必读）**：本 README 是 D 与 A/B/C/E 的对接基线。**每次代码或接口契约发生改动，必须在同一次提交里同步更新本 README**——接口形状、依赖契约、口径红线、环境变量、运行方式，任一项变化都要改，不得只改代码不改文档。

## 当前状态

- 已完成（不依赖 C/E）：应用骨架、统一错误格式、请求 ID、Argon2id 密码哈希、登录/会话/退出、登录失败限速、CSRF 与 Origin 校验、权限（viewer/admin）、全部 30 个路由注册、测试（**350 项全过**）。
- **已完成（2026-10-17 晚）：装上 C 0.2.1a2 + 迁到 0007**。安装件是包内 `data_foundation/release/anhui_data_foundation-0.2.1a2-py3-none-any.whl`（68006 B，sha256 `17c2c60045d4153507e66282a3f35d1583ed81332fb29654c8e0b86882c91c5b`，与 `DELIVERY-MANIFEST.json` 登记一致）；业务库 `tourism_flow` 与专用联调库 `tourism_flow_test` 均已由 0006 → 0007（新增 `visible_analyses` / `visible_count_points` / `visible_forecasts`，业务表 34 → 37）。`uow.visible`（`VisibleRepository`）与 `knowledge.invalidate_document`、`store.get_visible_input` / `store.get_document_file_ref`、`data_foundation.ingestion.run_ingestion_once` 均已就位。

- 已完成（接 C 0.2.0a2）：认证落库适配层 `app/services/c_auth_store.py`（`uow.auth`）；**知识链路的队列与索引改由 C 的 `jobs` / `knowledge` 仓储承载**（`app/services/c_store.py`），登录与 20→worker→21→22→19 全链路在**真实 MySQL 8.4.11 + 真实 C 包**上闭环冒烟通过（真机 12/12）。
- 已完成（接 E 0.6.1 / P2PNet）：`ServiceBindings` 注入（`app/services/e_bindings.py`，**loader 模式优先**）、D 自有 worker、**`activate_index` 与 `complete_job` 在同一 C 写事务内提交**、22 重建由 worker **逐文档展开**为 `knowledge_index` 子作业；录像计数口径**全面切换为 P2PNet 点计数**（`camera_visible_people`），DM-Count 口径废弃、旧 `count_model=dm_count` 报文被 schema 直接 400 拒收。技能自带平台冒烟 **53/53**。
- 已完成（2026-10-15 晚）：**23 录像列表 / 25 录像回放接上 C 的 `uow.video`**（回放含 Range 与 `no-store, private`，授权与摘要校验交给 C）；**26 号补上 E 的 `validate_analysis` 显式调用**（`device=GPU` / `openvino` / 未登记权重当场 422，不再异步失败）；**19 号注入受约束只读数值查询工具**（`app/services/metrics_query.py`）。
- **已完成（2026-10-16）：数据面接口不再一律「等 C」。** 复核 C 0.2.0a2 的 `UnitOfWork` 后发现 `catalog` / `observations` / `holidays` / `events` 四个仓储**早已提供**，此前把它们一律做成 503 是**过时判断**（与 23/25 同一次误判的同类问题）。现已接上 8 条：

  | 接口 | C 侧来源 | 要点 |
  |---|---|---|
  | 05 实体目录 | `uow.catalog.list_entities` | `available_metrics/grains` 只统计已核验观测（03 §05「不暗示未取得的数据可用」） |
  | 06 指标字典 | `uow.catalog.list_metrics` | `comparability_note` 原样透传 |
  | 07 来源详情 | `uow.catalog.get_source` | 找不到 → 404 |
  | 08 景区时序 | `uow.observations.query_series` | 指标-实体不符 → 422；来源不支持粒度 → 422 |
  | 09 交通时序 | 同上 | `transport_type` 由实体类型派生 |
  | 13 假期列表 | `uow.holidays.list_holidays` | `definition_source_id` 保证定义可追溯 |
  | 15 事件列表 | `uow.events.list_events` | C 只支持 `region_code`/`status`，其余筛选由 D 补齐 |
  | 16 事件详情 | `uow.events.get_event` | `source_url` 由 D 走 07 的来源仓储补齐 |

  库里没有数据**不影响接通**：接口返回 200 + 空列表，等 C 灌数据即可，不必再改代码。
- **已完成（2026-10-17）：24 录像上传接上 C 的三个入口**（`DataStore.persist_file` + `uow.provenance.register_asset` + `uow.video.register_video`），详见下节。此前记为「等授权记录」是**表述不准**——入口一直都有，缺的是**数据**。
- **已完成（2026-10-17）：10 驾驶舱总览 / 11 序列比较接上 C**（`uow.catalog` + `uow.observations.get_latest_verified` / `query_series` + `uow.events`），详见下节。两条此前被误判成「等 C 口径」，实际 C 的仓储完全够用——这是**第三次同类误判**（23/25 → 8 条数据面 → 10/11）。
- **已完成（2026-10-17）：12 历史预测 / 14 假期比较 / 17·18 事件写 / 29 采集作业接上 C**（`uow.forecasts.save_history_forecast` + `uow.observations.query_holiday_values` + `uow.events.create_event` / `update_event` + `uow.jobs.enqueue_or_get`）。至此 **30 条路由全部接完**，源码里不再有「等 C」的占位——剩下的 `pending()` 全部是 `cstore is None` 时返回 503 `dependency_not_available` 的**兜底**，不是未接线。

  ⚠️ 这又是**第四次同类误判**：12/14/17/18/29 一直被记成「C 仓储已有但口径待对齐、要等 C」，实际上 `uow.*` 全都有现成入口。**教训（写死在这里）**：说「等 C」之前，把该接口需要的每一份数据对着 `uow.*` 的方法逐个核一遍，不要凭印象归类。
- **已完成（2026-10-17 下午，C 021a2 收口清单）：按 C 的 10 条决定逐项对齐**，全部为纯 D 侧改动（除明确标注等 wheel 的）：

  | # | C 的决定 | D 的做法 | 状态 |
  |---|---|---|---|
  | ① | 扩枚举、保留真实数据 | 字典改从 C 导出的 `HOLIDAY_KINDS`/`EVENT_KINDS`/`EVENT_STATUSES`/`EVENT_SEVERITIES` 取（`app/services/c_enums.py`，装的是旧包时用同步副本兜底） | 完成 |
  | ② | 26—28 改走 C 的 `uow.visible` | **已接线（2026-10-17 晚）**：编排层 `app/services/visible_pipeline.py`；26 登记与入队同一写事务、27 从 C 读计数、28 预测结果落 C 的 `visible_forecasts`；worker 五步 `create` → `begin` → `write_points`（≤1000/批）→ `complete_analysis` + `fail_analysis`。**C 的 `complete_analysis` / `fail_analysis` 内部已结算 C job，D 外层绝不再 `complete_job`**（会 `LeaseLost`）。详见下节 | 完成 |
  | ③ | 时间线/④as_of 口径认可 D 现有做法 | 无需改动 | 完成 |
  | ⑤ | 18 必填 `expected_version` | schema 改为必填；缺失 **400**、冲突 409。**删掉「不传就代填当前版本」**（那等于替调用方关掉乐观锁） | 完成 |
  | ⑥ | 16/17/18 回显 `row_version` | 已在回显 | 完成 |
  | ⑦ | HTTP 分页/筛选由 D 补齐 | 沿用 | 完成 |
  | ⑧ | `day_index` 口径 | 沿用 `query_holiday_values` 的判定 | 完成 |
  | ⑨ | 历史预测不拆月 | 沿用 | 完成 |
  | ⑩ | `page_size` 上限要拦在 HTTP 入口 | `app/api/v1/_paging.py`，05/15/23 三条列表接口统一 `Query(ge=1, le=100)`，越界 → 400 `bad_request` | 完成 |

  另有 4 条 C 没写进清单、但明确要求 D 做的事，同步完成：
  - **Worker 按作业类型白名单领取**（`app/worker.py`）：只领 `knowledge_index` / `knowledge_reindex` / `knowledge_invalidate` / `video_analysis` / `visible_forecast` 五类并按轮转顺序轮转；**绝不用 `job_type=None` 裸领**——C 的 `jobs` 表里躺着它自己的 `ingestion` 作业，抢过来再甩「不支持」会直接污染 C 的队列。`knowledge_invalidate` / `visible_forecast` 两类已真实执行（2026-10-17 晚，`uow.visible` 就位后）。
  - **14 的 `daily_average` 删掉「用现有 daily_points 求均值」的兜底**：缺天时分母变小等于高估日均，现在只认 C 的「精确总量 ÷ 假期天数」；来源必须并上 `exact_total.source_ids`，不能只取 daily_points。
  - **ImportError 不再回退宽松快照**（`app/services/e_bindings.py`）：E 缺严格校验器就报 503，不用未经逐条比对的数据继续回答。
  - **19 号数值 resolver 接真实观测**：见下节。
- 待做：历史周/月预测与回测（算法核心已在 `app/services/historical_forecast/`，待 C 的周/月观测数据）；**真实视频闭环**（`videos` / `permission_records` 均为 0 行，**不造视频、不用合成片段关验收**）；29 的真实消费与幂等（入口 `run_ingestion_once` 已由隔离队列取证验过，**见下节**）。

## 版本约定（C/D/E 统一）

| 项 | 约定版本 |
|---|---|
| Python | 3.13.16（64 位），`pyproject.toml` 已限定 `>=3.13,<3.14` |
| 数据库 | MySQL 8.4.11 LTS |

## 环境准备（Windows PowerShell）

```powershell
cd api_platform
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
copy .env.example .env   # 按本机情况修改
```

C/E 的包到位后，追加安装（各目录同级摆放）：

```powershell
.\.venv\Scripts\python.exe -m pip install -e ..\data_foundation -e ..\intelligent_analysis
```

## 启动

```powershell
# API 服务
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000

# 后台作业进程（每周更新、知识库索引、录像分析；需 C/E 包）
.\.venv\Scripts\python.exe -m app.worker
```

接口文档：启动后访问 `http://127.0.0.1:8000/docs`。

## 测试

```powershell
.\.venv\Scripts\python.exe -m pytest
```

测试通过内存中的假数据仓储运行，不需要 MySQL，也不需要 C/E 的包。

## 对 C 的集成契约（重要）

应用启动时尝试导入 `data_foundation`，认证经 `app/services/c_auth_store.py` 适配层接入。

**C 包的现实接口（0.2.0a2 实测）**：顶层只导出

```python
def create_store(database_url, *, data_root=None, allow_sqlite_for_tests=False) -> DataStore: ...
```

**没有 `new_auth_store()`**。认证仓储在 `UnitOfWork.auth`（`AuthRepository`），方法为：

- `create_user(data: UserCreate) -> dict`
- `get_user_by_username(username) -> dict | None`
- `save_session(data: SessionCreate, now) -> None`
- `get_active_session(id_hash, now) -> {"session": …, "user": …} | None`
- `revoke_session(id_hash, now) -> bool`

适配层的两个关键处理：

1. **CSRF 派生**：C 的 `SessionCreate` 只有 `id_hash` / `user_id` / `expires_at`（`sessions` 表不存 csrf_token）。D 用服务端 `CSRF_SECRET` 对会话摘要 `session_token_hash` 做 HMAC-SHA256 派生稳定 CSRF，**不改 C 的表结构**。
2. **时区**：C 的 `utc_naive()` 要求入参 **tz-aware**，D 统一 `_as_utc(expires_at)` 后传入。

包不存在时，登录返回 503 `dependency_not_available`，应用其余部分正常运行。

### 知识链路的 C 接入（0.2.0a2）

知识任务的**唯一队列**是 C 的 `jobs` 表，D 是唯一消费者（E 不再领第二份队列）。D 侧的统一入口是 `app/services/c_store.py`（`CStore`，`SqliteJobStore` 的同形包装）：

- `create_store(database_url, data_root=...)` 构造；读写以 `store.write()` / `store.read()` 的事务上下文为单位（`uow.jobs` / `uow.knowledge`）。
- 队列：`claim_job` / `complete_job` / `fail_job` / `heartbeat` / `ensure_lease`，租约为 C 0.2.0a2 的三元组 `JobLease(job_id, attempt_no, lease_token)`（不再在内存携带 `expires_at`）。
- ⚠️ **`claim_job` 必须带 `job_type`**（一次只能筛一个类型，D 按白名单轮转逐个试领）。C 的 `jobs` 表里同时躺着 C 自己的 `ingestion` 作业（由 `data_foundation.ingestion.run_ingestion_once` 消费），**不带类型领取会把它们抢过来然后甩一句「不支持」标失败，直接污染 C 的队列**。D 只领上述五类；守卫用例见 `tests/test_c021a2_guards.py`。
- 索引：`persist_file`（原生落盘，返回 **DATA_ROOT 下的规范相对路径**）→ `begin_index` → `write_chunks`（**每批 ≤1000 片段**）→ `activate_index`（校验文件存在 + SHA256 + chunk 数 + 租约）→ `get_index_artifact_ref` / `get_chunks` / `get_document` / `list_documents`。
- **`activate_index` 只接受规范相对路径**：绝对路径会被 C 判 `InvalidArgument`（HTTP 400）。D 因此一律先经 `persist_file` 落盘再激活。
- C 的异常基类是 `DataFoundationError`（`NotFound` / `Conflict` / `InvalidArgument` / `LeaseLost` / `UnsupportedGrain` / `AnalysisNotReady`）。

**artifact 路径解析的现实约束**：`data_dir` / `detector_weights_path` / `video_tmp_dir` / `counts_storage_path` 是**目录**，`job_store_path` / `rag_index_path` 是**文件**。`app/core/config.py` 对两者分开探测（文件字段只探父目录）、探针文件名带 pid+uuid 唯一后缀——否则两个进程并发探测时后完成者 `unlink()` 失败，会把可写目录误判为不可写并**回退到 `%TEMP%`**，造成「worker 落盘根 ≠ API 读取根」的分叉（表现为 21 读 `pending` / 19 读 503）。回归用例见 `tests/test_config_paths.py`。

### 录像元数据与回放的 C 接入（2026-10-15）

C 0.2.0a2 的 `uow.video`（`VideoRepository`）**已经提供了完整取回能力**，D 不再把 23/25 一律当成「等 C」：

- **23 `GET /videos`** → `uow.video.list_videos(...)`：C 负责 scenic/camera 实体类型校验与 `status` 白名单；D 只透传分页并把公开列整成 `VideoOut`（时间统一 `+08:00`），**不返回内部存储路径**。
- **25 `GET /videos/{video_id}/playback`** → `uow.video.get_playback_ref(video_id, now)`：C 内部完成 ① 授权核验 `_permission(video, "replay", now)`；② 文件必须落在 DATA_ROOT 内（越界 `InvalidArgument`）；③ 文件存在；④ 重算 SHA256 + `byte_size` 与登记值比对（不一致 `Conflict`）。**D 不重新实现「谁能看」**。
  - 返回值含**服务器本地绝对路径**——只能用于 D 内部打开文件，**绝不出现在 HTTP 响应里**（响应只有字节流）。
  - D 负责流式响应本身：`Range`（单区间，416 + `Content-Range: bytes */<size>` 处理非法/越界）、`Accept-Ranges: bytes`、`Cache-Control: no-store, private`（03 §25 禁止共享缓存）。
  - C 的异常族在 `app/api/v1/_c_errors.py::raise_mapped_c_error` 逐类翻译（见下节）。
#### 24 录像上传（2026-10-17 接线，入口一直都在）

C 的三个入口**早就齐了**，之前挂 503 是 D 的判断滞后（与 23/25、05—16 同类）：
`DataStore.persist_file`（流式落盘，内容寻址 `raw/<source_id>/<sha256>/original<suffix>`，重复内容复用）→
`uow.provenance.register_asset`（登记原件，返回 `asset_id`）→
`uow.video.register_video`（登记录像）。

**「能不能传」全部由 C 判定**，D 不重新实现：授权是否有效且允许 `analysis`、景区与摄像头是否父子关系、原件/授权/录像三者 `source_id` 是否一致，都在 C 的 `register_video` 里（`InvalidPermission`→403 / `InvalidArgument`→400）。

D 只把形状：

- **格式白名单** `.mp4 / .mov / .avi / .mkv / .webm`，**大小上限 2 GiB**。03 §24 只写「限定格式、大小和时长」而**没给具体数值**，由 D 定（已回执 C）。
- **时长不做校验**：D 没有可靠手段（不引入 ffmpeg），如实标注，不假装做了。
- ⚠️ **`recorded_end_at > recorded_start_at` 必须在 D 侧拦**：C 的 `videos` 表有 `CHECK(recorded_end_at > recorded_start_at)`，违反走的是数据库 IntegrityError（**不在 C 的异常族里，会漏成 500**）。
- **授权在落盘之前取**（`uow.video.get_permission`）：授权无效时不产生垃圾文件，且能从授权记录派生 `source_id`（落盘目录要用）。
- **原件登记与录像登记在同一个 C 写事务内**（`CStore.register_uploaded_video`）：分两个事务会在中途失败时留下没有录像指向的孤儿 `source_assets`。
- 响应只给 `video_id` / `status` / `file_digest`（内容 SHA256），**不含 `relative_path` 或任何服务器路径**。
- 事务失败时**不删已落盘的文件**：路径是内容寻址的，同名即同内容，可能有别的原件指向它，删了会让别人的回放 404。孤儿文件不破坏一致性，清理交给运维。

⚠️ **当前没有真实数据**：`videos` 0 行、`permission_records` 0 行、`source_assets` 133 条全是文本类。所以 24 是「接口完整 + 单元测试覆盖 + 真机验 404 路径」，**未做真实视频闭环**。

### 数据面接口的 C 接入（2026-10-16）

**教训先写在这里**：23/25 暴露出「把 C 已提供的能力当成『等 C』」之后，本次复核了 `UnitOfWork` 的全部仓储，发现 `catalog` / `observations` / `holidays` / `events` 也早就可用。**判断「要不要等 C」之前，先读 C 的 `repositories.py`，不要沿用旧结论。**

分工原则与 23/25 一致：**口径归 C，形状归 D**——C 决定「哪些数据可用 / 指标与实体是否匹配 / 粒度是否支持」，D 只做参数校验与字段收敛。

- `app/api/v1/_c_errors.py`：C 异常族的**统一翻译层**（原先只写在 videos.py 里，现在所有接 C 的路由共用）。C 的异常类彼此不继承（全部直接继承 `DataFoundationError`），映射为：
  `NotFound`→404、`InvalidPermission`→403、`Conflict`/`LeaseLost`→409、`AnalysisNotReady`→409 `analysis_not_ready`、`MetricEntityMismatch`→422 `metric_entity_mismatch`、`UnsupportedGrain`→422 `unsupported_grain`、`QueryLimitExceeded`→400、`InvalidArgument`→400、其余 →503。**不认识的异常原样上抛，绝不吞成「空结果」。**
- `app/api/v1/_series.py`：08/09 共用的形状转换。C 返回的 `metric` 是**指标字典整条**（对外只取 `code`）、`coverage_ratio` 是 `Decimal`（转 float）、时间点一律 UTC aware（对外统一 `+08:00`，周期是 `date` 则输出 `YYYY-MM-DD`）。`points[].provenance`（`asset_url` / `source_locator`）与 `diagnostics` / `selection_policy_version` 属取数细节，**不进响应**。
- **15 的筛选项要特别注意**：C 的 `list_events` 只支持 `region_code` / `status`，03 §15 要求的 `entity_id` / `kind` / `authority` / `since` / `until` 与分页是 **D 在内存补齐**的（事件是公告级量级，全量取回可控）。**量级上升时应请 C 下沉到 SQL，而不是让 D 一直全表捞。**
- **16 的 `source_url` 不在事件列里**：D 用事件的 `source_id` 走 07 号同一个来源仓储补齐；来源记录缺失时给 `null`，**不因为来源没了就让整条公告 500**。
- 所有列表类响应统一 `Envelope`（`{"data":..., "meta":{...}}`），07/08/09/16 原先未包裹，本次一并对齐 03 §1。

#### ✅ 三处枚举差异已由 C 021a2 收口（2026-10-17：扩枚举、按 C 字典校验）

C 的决定是「**扩枚举、保留真实数据**」，并在包顶层导出常量，内容与根 03 同步：假期补 `new_year / spring_festival / qingming / dragon_boat / mid_autumn`（共 9 项），事件补 `closure` 与 `expired`。

所以 D 的处理从「一律透传」改为 **按 C 导出的字典校验**（`app/services/c_enums.py`）：

| 字段 | 取值来源 | D 的处理 |
|---|---|---|
| `holidays.kind` | `HOLIDAY_KINDS`（9 项） | 非法值 → 400，不再默默返回「空列表」冒充「这一年没有假期」 |
| `events.kind` | `EVENT_KINDS`（6 项，含 `closure`） | 同上 |
| `events.status` | `EVENT_STATUSES`（`active/resolved/expired`） | 同上 |
| `events.severity` | `EVENT_SEVERITIES`（4 项） | 同上 |

实现上**以 C 为准而不是 D 抄一份**：新 C 包（`≥0.2.1a2`）装得上就直接用它的常量，装的是旧包时退回与 C 021a2 文档同步的副本——升级 C 包无需改 D 代码，也不会因为取不到字典就静默放宽成空集合（那等于不校验）。

⚠️ **C 的 `video_analyses` 表仍是 DM-Count 时代结构，不能承载 P2PNet 分析登记**：`count_line` / `entry_side` / `openvino_version` / `detector_model_version` 是 **NOT NULL** 且 `interval_minutes` 有 `CHECK IN (1,5,15)`；而 E 0.6.1 口径（自 0.6.0 确立）是「无虚拟计数线、无进出方向、固定 5 分钟聚合、阈值固定 0.5」。**这条遗留结论曾经误导我们停在 D 自有表 `d_video_analyses` 上**，但 C 0.2.1a2 新增的 **`visible_analyses` / `visible_count_points` / `visible_forecasts`** 三张表正是为 P2PNet 准备的（`sample_count` / `quality_status` / `source_ids` NOT NULL、`grain` 支持 `frame`/`5min`，**没有**那些 DM-Count 列）。2026-10-17 晚起 **26/27/28 已全部改走 `uow.visible`（`VisibleRepository`）**，`d_video_analyses` 退役不再参与这三条链路。

### 录像分析（26—28）的 C 接入（2026-10-17 晚，`uow.visible`）

编排层是 **`app/services/visible_pipeline.py`**（新增），`e_bridge` 只做「E 协议 ↔ C 存储」的翻译，路由层不碰 C 的仓储。**第五次同类教训**：本节内容此前被写成「等 C 的 wheel」，而 wheel 装上后 `uow.visible.*` 早就全部就位——真正缺的是 D 侧改线，不是 C 的东西。

#### 26 `POST /videos/{video_id}/analyses`：登记与入队在同一写事务

`CStore.enqueue_visible_analysis` 把「登记 `queued` 分析」与「投递 C 作业」放进**同一个写事务**：只登记不入队会让 27 永远停在 `analysis_not_ready`，只入队不登记则 worker 查不到 `analysis_id`。两者原子，不留悬挂。

**检查顺序是有意的**（2026-10-17 修正）：`get_p2p_counter`（E 能力）→ `build_analysis_config` / `build_analysis_provenance` / `check_provenance_consistency`（C 侧形状与溯源）→ `e_bindings.validate_analysis`（E 的设备/引擎校验）→ **最后**才判断 C store 是否可用并可能 503。把 503 排在前面的后果是：非法请求在后端暂不可用时被掩盖成「重试即可」，调用方拿不到真正的 422/400。

- **config 八键**（`C_CONFIG_KEYS`）：`count_model` / `weights_version` / `confidence_threshold`（固定 **0.5**）/ `aggregation_minutes`（固定 **5**）/ `roi` / `inference_engine` / `device` / `sample_interval_seconds`。`openvino_version` 只在 OpenVINO 时才带，这里**显式丢掉**（C 的 `visible_analyses` 没有这一列，带上会被 `extra="forbid"` 拒）。
- **provenance 七键**（`C_PROVENANCE_REQUIRED`）：`weights_sha256` / `model_source_revision` 等。⚠️ **拿不到就报错，绝不编造**：溯源是让 B 能回答「这个数是怎么来的」的唯一凭据，造一个假 SHA256 等于埋一颗看不见的雷。
- `check_provenance_consistency`：config 与 provenance 的**重叠键必须一致**（权重版本写的 A、溯源却是 B → 直接拒）。
- 异常映射：C `NotFound` → 404（**包含「录像不存在」与「录像不是这一个」**）、`InvalidPermission` → 403（没有可用于分析的授权）、`(Conflict, InvalidArgument)` → 400。

⚠️ **未登记的录像现在是 404，不再是 202**（2026-10-17 起）：C 的 `VisibleRepository.create_analysis` 内部先 `locked(Video, video_id)` 取录像、再做 analysis 授权校验，录像不存在就抛 `NotFound`。**这不是回归**——旧行为是「给一条压根不存在的录像开 202 票据，等到 worker 才发现没有文件」，会留下一条永远跑不出结果的分析登记。本机 `videos` 表至今 0 行，所以随便一个 UUID 走 26 必然 404。

#### worker 五步序列（`app/worker.py::_run_video_analysis`）

`create`（幂等登记）→ `begin_analysis` → `write_visible_points`（**≤1000/批**，C 的 `write_points` 单批硬上限）→ `complete_analysis(expected_point_count=…)`（C 会核对点数，不符抛 `Conflict`）。失败走 `fail_analysis`。

⚠️ **C 的 `complete_analysis` / `fail_analysis` 内部已经把对应的 C job 结算掉了**，D 外层**绝不能再调一次 `complete_job`**——重复提交会撞 `LeaseLost` / `Conflict`。「C 内部已结算」这件事在 worker 里通过 `_job_already_completed` 标记表达。

#### 27 `GET /videos/{video_id}/analyses/{analysis_id}/counts`

直接读 `store.get_visible_counts(video_id=, analysis_id=)`，`status != ready` → 409 `analysis_not_ready`。

- ⚠️ **C 的 `get_counts` 没有 `grain` / `start` / `end` 参数**，一次给全量点 → **粒度与时间过滤必须在 D 侧做**（`filter_counts` / `_in_window`）。`frame` 按 `recorded_at` 比、闭区间命中；**`5min` 只保留完整位于查询区间内的窗口**（`period_start >= start 且 period_end <= end`）——一个 5 分钟点的数值是它自己窗口的中位数，把横跨边界的窗口算进来等于拿半个窗口冒充完整 5 分钟（C《致D和E-021a2接收核对与补充答复》§4 明确禁止，2026-10-17 晚由「交叠即命中」改过来）。时间比较**先归一成 UTC aware datetime 再比**，直接比 ISO 字符串会因为 `…T00:00:00+08:00` 与 `…T01:00:00+09:00` 是同一时刻而漏点。
- **换数据源不是取消防线**：`check_counts_shape()` 保留了对脏点的自查——缺 `value`（尤其是混进旧版 `entries` / `exits` 越线计数）、缺 `quality_status`、缺非空 `source_ids` 的点一律 422 拦下。脏点的后果是 `CountPoint` 直接 500 或安静返回 `value=null`（看起来「数据就是这样」），两种都比 422 糟。
- 审计两键 `weights_sha256` / `model_source_revision` 由 `_visible_provenance()` 从 **C 的 `visible_analyses.provenance`** 读取（不再读 D 自有表）。

#### 28 `POST /videos/{video_id}/analyses/{analysis_id}/forecast`

预测作业**独立于分析作业**：创建 `visible_forecast` 作业 → 立刻 `claim_job(job_id=…)` 领取**指定的那一条**（不能领到别的作业）→ `run_visible_forecast` → `publish_visible_forecast` 落 C 的 `visible_forecasts`。

- ⚠️ **`CStore.claim_job` 必须透传 `job_id`**（C 的 `jobs.claim_job` 有这个形参，D 这一层此前漏了 → 真机 `TypeError` → 500）。这一漏网就是因为「凭感觉方法应该有」而没逐个核签名，已用 `tests/test_store_call_surface.py` 的 AST 静态守卫钉住。
- ⚠️ **幂等键必须用定长摘要**：C 的 `jobs.idempotency_key` 是 **`varchar(64)`**（`jobs.type` 是 varchar(48)）。曾经直拼 `visible-forecast-{uuid}-{cutoff}-{horizon}` 共 82 字符 → MySQL `DataError 1406` → 500，而 **sqlite 测试库不校验列宽**，pytest 全绿只有平台冒烟能发现。现在用 `"vf-" + sha256(...)[:32]`，并在 `CStore.create_job` 入库前加 `_check_job_column_limits` 自查。**规矩：凡是写进 C 表的字符串，先查 `information_schema.columns` 的 `character_maximum_length`。**
- ⚠️ **先查存在性与归属，再开票**：分析不存在、或分析不属于 URL 上的那个 `video_id`，都**回 404**（跨录像拼装不回 409/403，避免对外暴露「它存在但不属于你」）。两者都在建作业**之前**判断——先建作业再撞 `NotFound` 会留一条永远跑不出结果的垃圾作业。
- ⚠️ **`_raise_mapped` 只认 E 的异常，认不了 C 的**：这段为此补了一层 C 异常族映射（`NotFound` → 404 / `InvalidPermission` → 403 / `Conflict`·`InvalidArgument` → 400）。**直接调 C 仓储的分支都要自己补这一层**，否则 C 的 `NotFound` 会一路穿到全局处理器变 500——2026-10-17 就是这样让未知分析返回了 500。错误路径上用 `_safe_fail_job` 标记作业失败：它自己再炸也不能盖掉真正的病因。
- **响应体读的是 C 落库后读回的那一条**（`_latest_forecast_result`），**不是 E 返回值的回显**——回显无法证明它真的存下来了。
- ⚠️ `ShortForecastResponse` / `ShortForecastPoint` 都是 `extra=None`，**会丢弃未定义字段**：`forecast_id` 进不了响应体，契约 §28 里也没有该字段，D 不擅自加。调用方通过 30 号 `/jobs` 的 `result_refs.forecast_id` 追溯。

#### 静态守卫（防止「单测全绿、真机 500」）

`tests/test_store_call_surface.py` 用 AST 扫 `app/**/*.py` 里所有 `<store>.<method>(...)` 调用点，与 `CStore` / `SqliteJobStore` 的**真实签名**逐项比对，抓「方法不存在」与「关键字参数不接受」两类问题；`IGNORE_METHODS` 排除认证 store 的同名方法，`CSTORE_ONLY` 显式登记 CStore 独有的形参（`create_job: idempotency_key/reason/requested_by`、`claim_job: job_id`）并写明理由，**不静默忽略**。断言 `checked > 40` 防止扫描逻辑自身失效。命令行同款脚本在 `temp/scripts/dpart_check_store_calls.py`。

背景：把两个新方法**误插到类外**、`fake` 恰好也有这些方法 → 单测全绿、真机才炸 500。这条防线就是为此存在的。

同一思路还落到两处：

- **`tests/fakes.py` 的 `FakeVisibleStore`（新增）**：刻意**复刻 C 的拒绝语义**——归属不匹配抛真 `NotFound`、未 `succeeded` 返回 `analysis_not_ready`、`write_points` 单批 >1000 抛 `InvalidArgument`、`complete_analysis` 点数不符抛 `Conflict`、`publish_forecast` 校验作业类型与 payload 三要素，`complete_job` 在作业非 `running` 时抛 `Conflict`（专抓「C 内部已结算、D 外层又结算一次」）。**替身越宽容，真机越吃亏**——这是本项目反复吃亏后定下的取向。
- **`tests/test_visible_pipeline.py`（新增 28 项）**：覆盖 config/provenance 组装（少键即报错、**不编造**）、worker 五步调用序列、2005 点分 3 批、点形状归一化白名单、27 号过滤（闭区间、时区安全、`grain` 不混）、22 与 `knowledge_invalidate` 的身份校验。

#### 四项验证（提交前的硬门槛）

用户的 push 授权以**四项全绿**为前提，缺一项就不提交：

| # | 验证 | 命令要点 | 2026-10-17 晚结果 |
|---|---|---|---|
| ① | 全量单测 | `env -u D_JOB_STORE_BACKEND ./.venv/Scripts/python.exe -B -m pytest -q -p no:cacheprovider`（**必须沙箱外前台跑**） | 392 passed |
| ② | 平台面冒烟 | 技能 `scripts/smoke.py`（需 uvicorn 8000） | 53/53 |
| ③ | 知识闭环冒烟 | 技能 `scripts/knowledge_smoke.py`（真跑 20→C jobs→worker→E→21→19） | 12/12 |
| ④ | C §六 七场景取证 | 技能 `scripts/c_acceptance_evidence.py`（只连 `tourism_flow_test`） | 16/16 |

⚠️ **①必须在沙箱外跑**：沙箱会拒写 `data/`，uvicorn/pytest 退到 `%TEMP%/api_platform_data`，`data_root` 与 artifact 相对路径错位，**凭空多出一批假失败**（见过一次 6 项）。同理，`run_in_background` 派生的进程会落回沙箱，起服务要前台跑 + 超时自动转后台。

### 驾驶舱总览与序列比较的 C 接入（2026-10-17）

**第三次同类误判**：10/11 此前也被记成「等 C 口径」，实际 C 的 `catalog` + `observations.get_latest_verified` / `query_series` + `events` 完全够用。**每次说「等 C」之前，先把该接口要的每一份数据对着 `uow.*` 的现有方法核一遍。**

#### 10 `GET /dashboard/overview`

参数 `region_code`（必填）+ `grain`（必填 `week|month`）+ `as_of`（可选）。响应 `cards[]` / `map_entities[]` / `active_events_count` / `data_freshness[]`。

三条口径，代码里都有对应注释：

1. **不同指标分卡，绝不求和成「总客流」**（03 §10 红线）。一次合计只碰同一个指标；机场指标在该地区没有适用实体时**根本不出卡**。
2. **同一指标跨实体求和，只加周期完全一致的点**。依据是 C 指标字典的 `aggregation_rule = complete_non_overlapping_periods_only` 与 `comparability_note`（「不同景区和时期须核对统计范围」）。具体做法：先挑出该地区**最新**的那个周期，落在该周期之外的实体不计入合计——所以「宏村已更新到 9 月、黄山还停在 8 月」时，合计是 9 月那部分，**不是两个数相加**。
3. **缺了谁要说清楚**：`status` 有 `ok` / `partial` / `no_data` 三值，配合 `entity_count` 与 `covered_entity_count`。`no_data` 时 `value` / `period_start` / `period_end` 全为 `null`，**不补零、不取均值、不用上一周期**。

两处刻意的设计：

- **单个实体不支持请求粒度时降级，不是 422**。C 抛 `UnsupportedGrain` 在单条时序接口（08/09）里就该如实 422，但在**聚合接口**里，一个实体缺 `month` 不该毁掉整张看板。这是总览特有的容缺，只吞 `UnsupportedGrain`，其它异常照常映射上抛。
- **`as_of` 不传时回显「数据实际截至日」而不是今天**。03 §10 说默认「数据库最新已核验日期」；C 没有直接给这个日期的入口，而 `get_latest_verified(as_of=today)` 本身就会回退到最新已核验点，效果等价。回显取卡片里最大的 `period_end`，一张卡都没有时才回退到今天。⚠️ 这条口径**待反馈 C**：是否需要一个「全局最新已核验日期」的仓储入口。

⚠️ **C 的 `list_entities` 的 `region_code` 是精确相等**（`Entity.region_code == region_code`），不是前缀匹配。传 `340000`（安徽省）匹配不到 `341000`（黄山）的实体。已回执 C，当前按精确匹配实现。

#### 11 `POST /analytics/compare`

2—4 条序列（每条 `entity_id` + `metric`）+ `grain`（`day|week|month`）+ `start` / `end` + `display_mode`（`separate_axes|index_100`）+ `base_period`（`index_100` 时必填）。

- **口径由 C 判**：指标-实体不匹配 → 422 `metric_entity_mismatch`；来源不支持该粒度 → 422 `unsupported_grain`；实体/指标不存在 → 404。D 不自己实现这些判断。
- **混合指标不能同轴原始值比较**：`index_100` 下点位值换成无量纲指数、`unit` 置为 `index`；原始单位放在序列级 `unit`，基期数值放在 `base_value`——否则调用方拿到一串 100 左右的数，却不知道它是拿什么除出来的。
- **基期取不到有效值就整条 400**，绝不返回「一半指数一半原始值」——那正是契约最担心的「看似可比实则不可比」。
- `comparison_note` 逐条写清：涉及哪些指标、C 指标字典 `comparability_note` 的**原文**、每条序列覆盖了多少期、指数化的基期。
- `base_period` 按「`period_start <= base_period <= period_end`」匹配所在周期（03 §11 说它是「日期或周/月起始日」）。

#### 两处共用的补充

- 10 的卡片值求和用 `Decimal` 逐项累加、最后才转 float（C 用 Numeric 存观测值），避免浮点累加误差。
- 10/11 的响应都统一 `Envelope` 包裹（与 05/06/07/08/09/15/16 对齐 03 §1）。

### 预测·假期比较·事件写·采集作业的 C 接入（2026-10-17）

**12 历史预测**：观测序列来自已接通的 `uow.observations.query_series`，算法是 D 自有的
`app/services/historical_forecast.run_history_forecast`，结果落 `uow.forecasts.save_history_forecast`。

- **数据不足就 `status=data_insufficient` + 空 `forecast_points` + `reason`，绝不用历史均值伪装成预测**。
- C 的两条硬校验 D 必须自己先满足，否则是 500：`status=ready` 时点数必须等于 `horizon`；
  `data_insufficient` 时必须无预测点且有 `reason`。
- 预测是有副作用的写操作，**入不了库就必须让调用方知道**（C 异常族翻译出的 4xx/5xx），
  绝不「算出来了但没存」还返回 200。

**14 假期比较**：口径**全部交给 C 的 `uow.observations.query_holiday_values`**——
它一次性给出 `day_index_supported` / `daily_grain_supported` / `missing_days` / 假期总量 `exact_total`。

- 我最初手写了一版「用 `query_series` 取日粒度再自己算日均」，那是**重复实现口径**，
  而且分母用「有数据的天数」，与 C 的「总量 ÷ 假期天数」不一致，**C 的才对**。已改用 C 的权威判断。
- 来源只有假期总量时**不摊成逐日曲线**（03 §14 红线），记成不可用并写进 `reason`，不让整个接口 422。

**17 / 18 事件写**：`uow.events.create_event` / `update_event`。

- ⚠️ **`asset_id`（事件原件）03 §17 没写，但 C 的表里是 NOT NULL 且直接下标取**——
  缺失会走 `KeyError` / `IntegrityError` 漏成 500。而 C 的 `provenance` 仓储**只有
  `get_asset(asset_id)`，没有按来源查原件的入口**，D 无法从 `source_id` 反查，
  也不能凭空造一条原件（那是伪造溯源）→ **只能要求调用方显式给**。已回执 C。
- ⚠️ **`expected_version`（乐观锁）03 §18 也没定义**，响应里原先还看不到版本号。
  D 把它回显在 `row_version` 里；调用方不传时 D 代填当前版本（等于放弃并发保护）。已请 C 补进契约。
- 重新激活已解除事件必须给 `new_source_id`（03 §18）；因为 **C 只在 `asset_id` 一并提交时
  才校验「来源与原件一致」**，换来源时 D 要求同时给 `new_asset_id`，否则事件会停在
  source 与 asset 不匹配的状态而 C 不拦。
- ⚠️ 时间线映射：C 的 `event_history` **没有** `kind` / `at` / `source_id` / `note` 四列，
  只有 `version` / `snapshot` / `change_note` / `changed_at`。`at` ← `changed_at`、
  `note` ← `change_note`、`source_id` ← `snapshot['source_id']`（**该版本生效时的来源**）、
  `kind` 由 `version` + `snapshot['status']` 推导（1→announce，快照 resolved→resolve，其余→update）。
  只用了 C 自己存的字段，不编造取值。（按假列名写会返回全 None → 校验失败 → 500，真机才暴露。）

**29 采集作业**：`uow.jobs.enqueue_or_get`。**只入队，不执行**——导入由 C 的 worker 领租约后跑，
D 不越界代跑。`dry_run` 默认 `true`（03 §29）；幂等键只由 `source_ids` + `dry_run` 构成，
**`reason` 不进幂等键**（换个理由重复触发应命中同一个作业）。

**30 作业状态读回**：`uow.jobs.get_job`。

- ⚠️ **C 后端返回的 `started_at` / `finished_at` 是 `datetime` 对象，不是字符串**（2026-10-18 真机
  首次暴露，之前 sqlite 替身里一直是字符串 → 替身比真源宽容，`_to_cn_tz()` 的 `fromisoformat`
  直接抛 `TypeError` 漏成 500）。已改为**先判类型**：`datetime` 直接 `astimezone(东八区)`；
  只有字符串才走 `fromisoformat`；其余类型原样返回不抛。守卫测试见 `tests/test_jobs_status.py`。
- 时区口径不变：作业库按 **UTC** 存储，输出统一转 `+08:00`。

### 真实录像全链路首次真机跑通（2026-10-18）

C 的隔离输入包 `MOT20-01-isolated-input-2026-10-17`（一段 17.16 秒固定高位室内通道 MP4，
13,110,890 字节）到位后，**24 → 26 → 27 → 28 → 29 → 30 第一次用真实输入端到端跑通**：

- **24** 登记进 C 的 `videos` / `permission_records`（由 C 的 `verify_d_four_items.py --apply` 重建）。
- **26** 创建分析 → D worker 领租约 → **真实 P2PNet 推理**：抽出 2 个 frame 点（t=0 → 34.0 人，
  t=10 → 43.0 人），模型溯源 `weights_sha256=50604773…`、修订 `team-zip-2981dc75` 一并落进 C 的
  `visible_analyses` / `visible_count_points`。
- **27** 计数读回正常；**5min 粒度仍报 `data_insufficient`**——17.16 秒凑不出一个完整 5 分钟窗，
  这是**正确行为**（C 已明确：这条不能用短片验收 ready）。
- **28** 短时预测同理由 `data_insufficient`，不伪装成 ready。
- **29 → C 消费 → 30** 全程有原始响应留证。

⚠️ **C 的 `verify_d_four_items.py` 在中文 Windows 上有个编码 bug**：脚本里 `Path.read_text()` 未指定
encoding，本机 GBK locale 下解 UTF-8 文档会 `UnicodeDecodeError`。绕法是
`python -X utf8` 或 `PYTHONUTF8=1` 运行（已回执 C 建议修）。

## 对 E 的集成契约（重要）

`intelligent_analysis` 的服务函数由各路由经 `app/services/e_bridge.py` 调用，路由层不直接 import E 内部模块。当前按 **E 0.6.1**（`intelligent_analysis.integration.ServiceBindings`；录像计数为 **P2PNet 点计数**）的实际能力对接：

**ServiceBindings 注入**（E 0.5.1 起移除自带 `AnalysisWorker`，改为依赖注入）：D 在 `app/services/e_bindings.py` 实现 E 的 `ServiceBindings` 协议，应用启动（`main.py` lifespan）与 worker 启动时幂等调用 `configure_e_services()` 注入：

- `submit_job`：D 真实入队（knowledge_index / video_analysis / knowledge_reindex），**20 号上传只入队这一次**（E 的 `index_document` 内部经绑定入队，路由层不再重复入队）；knowledge_index 作业顶层必须带 `document_version`；
- `load_counts`：从**C 的 `uow.visible`** 读回分析计数（`analysis_id` 不存在或不属于该 `video_id` → 404；已登记未完成 → 409 `analysis_not_ready`）。**返回体只含 E `CountsResponse` 的 10 个顶层键**（`extra="forbid"`），审计字段 `weights_sha256` / `model_source_revision` 由 D 路由层从 **C 的 `visible_analyses.provenance`** 补（2026-10-17 起不再读 D 自有表）；
- `validate_analysis`：用 **E 的 `P2PNetCounter.validate_config`**（`P2PNET_BUNDLE_DIR` 未登记 → 422 `unsupported`）。⚠️ **26 号必须显式调它一次**：26 号绕开了 E 的 `service.analyze_video`（后者不透传 `analysis_id` 与 `file_path`），而 E 的校验正是在那一层调用的；D 不走那条路就必须自己补这一刀（`e_bridge.analyze_video` 内 `e_bindings.validate_analysis(e_config)`），否则 `device=GPU` / `openvino` / 未登记权重会一路 202 入队、直到 worker 才异步失败。规则由 E 的校验器定，D 不重新实现（见 `tests/test_p2p_bundle.py` 真机用例：`pytorch + CPU` 通过，其余 422）；
- `knowledge_snapshot_loader`（**默认，loader 模式**）：**主路径走 E 的 `load_verified_knowledge_snapshot`（`intelligent_analysis.knowledge.artifacts`，E 0.5.1+）**——D 只提供「授权文档 ID 列表」`CStore.ready_document_ids()`（`index_status == "ready"`）与 `data_root`，逐项语义对账全部由 E 完成：产物文件存在 + SHA256、`schema_version` / `parser_version` / `embedding_model_version`、文档身份、`index_version`、**`document_version`**、metadata 逐字段、**chunk 逐条比对（order / chunk_id / locator.order / text 必须与 C 的 KnowledgeChunk 行一致）**。任一文档不通过即**整体失败**（all-or-nothing），映射 **503**，绝不用旧内存回答；
  - ✅ **为什么必须是主路径**：E 的 `service.answer_message` 只把 loader 结果交给 `InMemoryKnowledgeIndex.from_payload()`，而 `from_payload` **只校验 chunk 的文档归属**，不比对 C 库文本 / locator / document_version。也就是说 E 包里那套 `load_verified_knowledge_snapshot` **定义了但没人调用**——D 若只喂自己拼的 dict，「产物被改写但摘要同步改对」就会返回 200 而不是 503（C §六 场景⑧ 实测失败，2026-10-15 已修复）。
  - ⚠️ `CStore.snapshot_for_e_publisher()`（宽松版，**只做形状对齐、不做语义对账**）**仅在 E 无 `knowledge.artifacts` 模块（< 0.5.1）时兜底**；校验失败**绝不退回宽松版**——那等于绕开 all-or-nothing 用脏数据继续回答；
  - ⚠️ **all-or-nothing 的运维含义**：任一 ready 文档的活动产物缺失，就会让 19 号整体 503（本机 2026-10-15 实测：一条遗留文档产物被清理 → 全部问答 503）。修复方式是**对该 `document_id` 走 22 重建**（staging 源文件还在即可），重建成功后立即恢复 200；
- `knowledge_index_path`（**仅回退**）：只有拿不到 C store（SQLite 桥接 / 测试）时才退化到路径模式。E 强制两者互斥，D **优先 loader**。

**worker（`python -m app.worker [--once]`）**：D 自己领取作业后调 E 的 `dispatch_analysis_job`（在 `intelligent_analysis.worker.runner`，不在包顶层）。**knowledge_index 的提交顺序**（E 0.5.1 清单 §五）：`领取并退出短事务 → 事务外解析构建 + C 原生 persist_file 落盘 → 短事务 begin_index → 分批短事务 write_chunks（≤1000/批） → 同一短事务 activate_index + complete_job → 发布全量活动快照`。要点：

- **文件必须先于激活存在**；`activate_index` 与 `complete_job` 由 `CStore.activate_and_complete` 共用**同一个写事务**，任一步异常都向传播出事务并整体回滚（不捕获后继续提交）；
- `parser_version` / `embedding_model_version` 取 E 返回值（`text-parser-0.5.0` / `keyword-tfidf-0.5.0`），D 不再硬编码；索引版本每次发布递增（重建不能复用已发布版本）；
- **坏摘要（SHA256 不符）不进入活动版本，作业失败**；
- `video_analysis` 在 **P2PNet 模型包未登记**或 `file_path` 缺失时**诚实失败**（`fail_analysis` 写明原因），不伪造结果；写点后必须调 `complete_visible_analysis`（否则 27 恒 409）。详见上节「worker 五步序列」——注意 **`complete_analysis` / `fail_analysis` 内部已结算 C job，D 外层不得再 `complete_job`**。

**22 重建（knowledge_reindex）**：E 的 `reindex_documents` 只入队一个 `knowledge_reindex` **信封**，自己不生成子作业；**逐文档展开是 D worker 的职责**——D 领取信封后为每个 `document_id` 重新入队一个 `knowledge_index` 作业（复用已登记的原件），子作业走上面同一条五步序列。找不到原文的文档记入 `result_refs.skipped`，**不静默当作已重建**。

⚠️ **重建原文取 C 的登记，不取本机 staging**（2026-10-17 晚按 C §6 改）：顺序是 **C `get_document_file_ref(document_id)` → CStore.get_document_file_ref → 本机 staging 兜底 → payload 的 `file_path`**。真机返回 `file_path`（C DATA_ROOT 下绝对路径）/ `sha256` / `document_version` / `relative_path` / `asset_id`；以前重建完全依赖 `data_dir/knowledge_staging/{document_id}.*`，那是**上传过程的临时痕迹，清理一次就再也重建不了**，而 C 库里的登记才是权威。

**20 号上传**：`document_id` 由 D 生成 UUID（不再由文件名推导）；`document_version` 取**原件内容 SHA256 的前 32 位**、前缀 `sha256:`（即 `sha256:<32位hex>`，如 C manifest 的 `sha256:29aa803a0fa3e009feed5ce3aa07330e`）——由 `e_bridge.document_version_from_bytes()` 计算，**同一值**写进 C `KnowledgeDocument.document_version` 并随 payload 传给 E。**不得自造递增版本号（如 `"v1"`）**：E 的 `knowledge/artifacts.py::_validate_document` 会在加载产物时校验 `document_version` 与 C 库登记值一致，不一致即拒绝加载。C store 可用时执行三段登记（`persist_file` / `register_source` / `register_asset` / `register_document`），staging 原文件落 `data_dir/knowledge_staging/`。

⚠️ **`index_version` 与 `document_version` 在 E 0.6.1 起已解耦**（2026-10-18 升级实测，旧的「二者相等」结论作废）：`service.index_document(file_path, metadata, index_version=None)` 新增 `index_version` 形参，**不传时 E 自行生成 `e-index-<uuid4 hex>`** 并填入 payload 顶层 `version`；`document_version` 仍为 C 口径 `sha256:<32hex>`，二者**不再相等**。E 的 `knowledge_worker` 正是按 `document_version = payload["document_version"]`、`index_version = payload["version"]` 分别读取的。D 的 worker 执行时**以 C 口径覆盖**：`worker._resolve_index_version()` 取 C 上已发布版本 +1（无则 `v1`，重建必须前进，否则 C 的 `begin_index` 对 ready 索引直接 Conflict），产物里写的也是这个 `v<n>`。因此 `e-index-*` 只是**入队到执行之间的中转值**，最终以 `v<n>` 为准；排查时不要拿入队 payload 的 `version` 去对产物。

**受约束只读数值查询工具（`metric_tool`，E P1；`app/services/metrics_query.py`）**：E 0.6.1 的 `service.answer_message` 在 `context` 含 `metric_query` 时**完全改走数值路径、不再检索知识库**，并要求工具返回值必须含 `metric` / `value` / `source_ids` / `period_start` / `period_end` / `queried_at` 六键且 `source_ids` 非空，缺任一键 E 就 `raise ValueError`（经全局映射会变成 500）。D 的约定：

- **注入时机（保守）**：只有请求 `context` **显式给出** `metric` 时才注入 `metric_query` + `metric_tool`。只带 `entity_ids` / 时间范围不给 metric，仍走知识库检索——否则「顺手带个范围」的文档问答会被数值分支整条掐掉；
- **指标字典白名单**（与 03-接口总览 §1 同源，新增指标先改那儿再改 `METRIC_CATALOG`）：`scenic_visits`（人次）/ `rail_departures`（人次）/ `airport_throughput`（人次）/ `camera_visible_people`（人）。非法指标在 schema 层即 **400 `bad_request`**；
- **收敛规则**：对象类型 / 粒度只能取字典内值，`entity_ids` 必须是 UUID 且 ≤20 个，`period_start` / `period_end` 必填且为带时区 ISO 8601、起止有序。任一条不满足 → `{"status": "metric_tool_unavailable", "reason": ...}`，**且返回体里绝不带 `value` / `source_ids`**（防「半个成功」把 E 打成 500）；
- **观测类指标已接真实数据（2026-10-17）**：`scenic_visits` / `rail_departures` / `airport_throughput` 走 C 的 `uow.observations.query_series`（`quality="verified"`，与 08/09/10 同一条口径）。取值规则：
  - 区间恰好是**一个完整周期**（如月度查询 2025-01-01~2025-01-31）→ 返回那一点的值 / 单位 / 来源与它**自己的周期**（先验值：合肥新桥机场 2025-01 = **1,111,644** 人次）；
  - 区间跨多个周期 → **只有**所有周期都有已核验观测（`missing_periods` 为空）**且** C 指标字典的 `aggregation_rule` 允许时才合计；缺一期就整条报不足（与 14 号「缺天不能用已有天数做分母」同一条红线）；
  - **一次只核实一个对象**：跨对象合计属于看板 TOP-N 口径，问答里宁可报不足（`multi_entity_not_supported`）；
  - **带时区的输入先转 +08 再取日期**（2026-10-17 晚按 C §6 修）：`2024-12-31T16:00Z` 的北京业务日是 **2025-01-01**，直接 `.date()` 会得到 2024-12-31，跨年时整段错一年。`_parse_period` 对 aware 输入统一 `astimezone(+08:00)`；
  - **「查不到」要分清两种原因**（2026-10-17 晚，真机实测后发现）：C 的 `query_series` 对**未覆盖完整周期**的区间返回 `points=[]` **且 `missing_periods` 也是空的**（实测合肥新桥机场：`01-01~01-20` / `01-15~02-15` / 单日三种情形都是空）。这时报 `no_verified_data` 是**误导**——观测明明存在（整月查得到 1,111,644），真实原因是「这个区间不构成完整周期」。现在这种情况报 **`incomplete_period`** 并在 detail 里说明要把区间对齐到周期边界；只有真的缺周期观测才报 `no_verified_data`；
  - **逐点可用性检查**：单位必须与指标字典一致、必须是 `verified`、必须带**非空** `source_ids`，任一条不满足即整条报不足（`unusable_points`）。这一条不能省——E 拿空 `source_ids` 会 `raise ValueError`（经全局映射变成 500）；
  - C 的异常（`NotFound` / `MetricEntityMismatch` / `UnsupportedGrain` / `QueryLimitExceeded` / `InvalidArgument` 及任何未知异常）一律翻译成 `metric_tool_unavailable` + reason，**绝不让异常穿到 E 里炸 500**。
- **区间的开闭语义做了「宽容归一」（2026-10-17 晚）**：C 的参考适配器按 `[start, end)` 写，D 的 08/09/10/14 与 C 的 `SeriesQuery` 按闭区间写，两者只在「end 恰好落在下一个周期起点」时分歧。`_normalise_window()` 让两种写法指向同一批周期（`grain=month` 且 end 是某月 1 号且与 start 不同月 → 右端点回退一天；`week` 同理；`day` 保持闭区间）。**真机已验：`2025-01-01~2025-01-31` 与 `2025-01-01~2025-02-01` 都拿到 1,111,644**。
  ⚠️ **C 的答复（同日晚，见下注）**：C 的 SDK `SeriesQuery` 继续用**闭区间 date**，C 参考数值适配器继续用**带时区的 `[start, end)` 完整业务日**——**这两个入口不能混成一个「按 grain 猜端点」的通用规则**。所以 `_normalise_window()` 只是 D 的**兼容层行为**，不改 C 的定义；落在周期腰部的区间一律报不足，**绝不把内部完整周期的小计冒充成整个请求区间的值**。
- **`camera_visible_people` 继续报不足**：它依赖真实录像分析结果，而 C 的 `videos` / `permission_records` 至今 0 行。**没有素材就没有数值**——不给这个项目签假验收的机会。
- **不外泄**：工具返回值会经 E 原样塞进 `metric_result` 回到前端，因此 detail 只写 `source_public`（接口级），内部表名 / 目录路径 / `blocked_by` 只进日志。

**异常映射**（`e_bridge._raise_mapped`）：E 的 `DependencyUnavailable` → 503 `dependency_not_available`；`UnsupportedAnalysis` → 422 `unsupported`；E 内部的 `analysis_not_ready` → 409；C 的 `DataFoundationError`（loader 路径）同样收敛为 503。问答快照损坏或缺失时一律 503，不用旧内存回答。

**形状转换**：D/B 公共契约的 ROI `{"points": [[x, y], ...]}` ↔ E 内部 `{points: [{x, y}]}` 双向转换；E 的 `persistence_mae` 映射为 D/B 的 `baseline_mae` 并透传 `fold_count`；26 号透传 config 用 `exclude_none`（pytorch 时不带 `openvino_version`）。

录像 26—28 的指标固定为 `camera_visible_people`（**P2PNet 点计数**在固定 ROI 内聚合得到的**估计可见人数**）。**红线：P2PNet 模型包未登记或 E 能力不满足时返回 422 `unsupported` / 作业诚实失败，绝不用 `entries/exits` 换算或密度图求和方法伪造画面人数；`count_model` 只接受 `p2pnet`，旧 `dm_count` 报文在 schema 层即被 400 拒收，不回退。**

## 与前端（B）确认的 JSON 形状（联调基线）

B 的 `README`「A/B 前端交接与 D 端联调约定」把未定字段具体化，D 已按此实现：

- **预测区间**（12、28）：`forecast_points[].lower_bound` / `upper_bound`，number 或 null；缺失保持 null，不补造。
- **`Counts.roi`**：归一化 `[x, y][]` 多边形，至少三个点（每点是两个 0—1 的数）。
- **分析元数据**位于计数响应**顶层**：`count_model`（固定 `p2pnet`）、`weights_version`（固定 `leader-SHTechA-50604773`）、`confidence_threshold`（固定 `0.5`）、`weights_sha256`、`model_source_revision`（后二者为 D 侧审计字段，不经 E）、`inference_engine`、`device`、可选 `openvino_version`、可选 `sample_interval_seconds`。
- **计数点**：通用数字字段；`grain=5min` 时另要求 `sample_count` 与 `aggregation_rule=median`。`unit` 固定中文「人」，`quality_status ∈ {good, degraded, unavailable}`（**无 `ok`**）。
- **短时预测**：独立预测类型保留 `training_cutoff`、`input_window_minutes`、`model_version`；`validation_error` 为按 `15/30/60` 分钟索引的对象或 `null`，各项含 `mae`、`baseline_mae`、`sample_count`、`method`。
- **预测点**：通用数字字段 + `grain=5min` + `is_forecast=true`，时间为未来时间点（`period_start=period_end`）。
- **跨景区比较**（11）：`series[]`（`entity_id`、`metric`、`points`、`missing_periods`、`source_ids`）+ `comparison_note`。
- **计数点缺帧**：`CountPoint.value` 可为 `null`（unavailable 缺帧不填 0 掩盖）；`grain=5min` 时另含 `sample_count` 与 `aggregation_rule`。
- **验证误差**（28）：`validation_error` 各项另含 `fold_count`（E 0.5.0 透传，可为 null）。
- **问答**（19）：`ChatResponse` 顶层带 `metric_result`（查询时间/指标/周期/来源溯源，未用指标查询时为 null）；`ChatRequest` `extra="forbid"`，拒收用户注入 `metric_query` / `metric_tool` 可执行对象。`ChatContext` 新增**可选** `metric`（只取指标字典四值，非法即 400）——**只有显式给 `metric` 才走数值核实路径**，不给则维持原文档问答行为（对 B 向后兼容）。
- **短时预测跨度**（28）：`horizon_minutes` 请求层仍接受 15/30/60，但 **E 0.6.1 内部仍关闭 60 分钟**（自 0.6.0 起）（`short_forecast/baseline.py`：`60-minute forecasting is disabled pending multi-day validation`），D 原样透传其 `status=data_insufficient` + `reason`，**不在 D 侧改写成 422，也不自行生成 60 分钟预测点**。
- **⚠️ 2026-10-17 变更（B 需同步）**：**24 `POST /videos` 已从 503 变为真实路由**。管理员 + `X-CSRF-Token` + `multipart/form-data`；表单字段 `file`（必填）/ `scenic_id` / `camera_id` / `recorded_start_at` / `recorded_end_at` / `permission_record_id`（均必填）/ `camera_location_note`（可选）。限制：**格式 `.mp4/.mov/.avi/.mkv/.webm`、大小 ≤ 2 GiB**（时长不校验）。成功 201 `{video_id, status:"uploaded", file_digest}`；失败码：格式或大小或时间顺序 → 400、授权不存在 → **404**、授权失效/不允许分析 → **403**、景区与摄像头关系无效 → 400。**注意现在库里没有任何授权记录，所以必然 404**——这不是 bug。另有 **13/15 的字典取值可能超出你手上的枚举**（`new_year` / `expired` / `closure`），见上文，等 C 定夺前请按「可能有未知值」兜底渲染。
- **⚠️ 2026-10-16 变更（B 需同步）**：05 / 06 / 07 / 08 / 09 / 13 / 15 / 16 八条**已从 503 占位变为真实响应**，且 07 / 08 / 09 / 16 的响应**统一加进了 `{"data":..., "meta":{...}}` 包裹**（原先裸返回）。字段要点：
  - 05 每项只有 03 §05 的 9 个字段，`longitude` / `latitude` **可为 null**，`available_metrics` / `available_grains` 只列**已核验**观测支持的能力；
  - 06 每项含 `comparability_note`（口径说明，前端应展示，不要自行拼接指标）；
  - 08 / 09 的 `metric` 是**指标代码字符串**（不是字典），`coverage_ratio` 是浮点，`points[]` 十项溯源齐全、`period_start/end` 为 `YYYY-MM-DD`、时间为 `+08:00`；09 多一个 `transport_type`；
  - 15 列表项**不含** `description` / `operator_note` / `timeline`（只在 16 详情里）；
  - 错误码新增两类 422：`metric_entity_mismatch`（指标与实体对不上 / 实体不是该接口要求的类型）、`unsupported_grain`（来源不支持该粒度）。

## 安全基线（团队文档要求）

- 密码仅保存 Argon2id 自适应哈希；原文不写日志。
- 会话仅存服务端（`sessions` 表，C 维护），浏览器只持有 `HttpOnly; Secure(生产); SameSite=Strict; Path=/` Cookie；Cookie 内是随机会话令牌，库中只存其哈希。
- 登录失败限速，超限返回 429。
- 未登录 401；权限不足 403；登录后写操作要求 `X-CSRF-Token` 并校验同源 `Origin`。
- 退出登录立即撤销服务端会话。

## 环境变量（`.env`，由 `.env.example` 复制）

| 变量 | 说明 |
|---|---|
| `DATABASE_URL` | MySQL 连接串（与 C 共用；本机用 `dimport` 账号） |
| `CSRF_SECRET` | CSRF 派生用的服务端密钥；**生产必须强随机固定值**，留空则启动随机生成并告警 |
| `ALLOWED_ORIGINS` | 登录同源白名单（正式前端地址） |
| `SECURE_COOKIES` | 生产置 `true`（HTTPS 下 Cookie 带 `Secure`） |
| `SESSION_TTL_MINUTES` | 会话有效期 |
| `D_JOB_STORE_BACKEND` | 作业库后端：`c_store`（**默认**，走 C 的 `jobs` / `knowledge` 表）或 `sqlite`（D 桥接，仅本地回环与测试用） |
| `DATA_DIR`、`VIDEO_TMP_DIR`、`COUNTS_STORAGE_PATH` | 运行时**目录**（自动创建；写探测失败才回退 `%TEMP%` 并告警） |
| `JOB_STORE_PATH`、`RAG_INDEX_PATH` | 运行时**文件**路径（只探测父目录）；`RAG_INDEX_PATH` 现仅作路径模式回退与本地留档，正式问答走 C loader |
| `P2PNET_BUNDLE_DIR` | **P2PNet 模型包目录**（含 `SHTechA.pth`、`models/`、`util/` 与权重清单）；启动时登记为 `P2PNetCounter.from_team_bundle` 的 bundle 根。**未登记时 26 号返回 422 `unsupported`，`video_analysis` 作业按「P2PNet 模型包未登记」失败（诚实失败，不伪造）** |

## 口径红线（写业务代码前必读）

1. `景区接待人次`、`铁路发送人次`、`机场旅客吞吐量` 是不同指标，不直接相加。
2. 来源只有月度数据时保持月度粒度，接口对不支持的粒度返回 422，不伪造周数据。
3. 预测数据不足返回 `data_insufficient` 与空预测数组，不用历史均值伪装预测。
4. 视频结果一律标注「录像回放」与「**指定画面区域估计可见人数**（`camera_visible_people`）」，不呈现为入口进出人次或全景区实时在园人数；**不得回退为未标明的 YOLO 越线计数**。
5. 知识问答只依据 **C 上已激活且产物可校验**的索引：活动产物缺失或损坏时返回脱敏 503（提示重建索引），**不用旧内存快照回答**；重建必须换新索引版本，切换期间旧版本继续服务。
