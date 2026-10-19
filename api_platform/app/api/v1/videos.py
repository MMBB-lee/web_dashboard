"""录像画面人数计数与短时预测接口（03-接口总览 §8：23—28）。

2026-10-15 版契约（E 0.6.0）：指标为 `camera_visible_people`（指定摄像头固定画面
区域的估计可见人数，由 **P2PNet 点计数**在 ROI 内聚合）；没有虚拟计数线、
进出方向或累计人次。页面口径约束：一律为「录像回放」与「指定画面区域估计
可见人数」，不得呈现为真实实时客流或全景区在园人数。
"""

from datetime import datetime
from pathlib import Path
import anyio

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse

from app.api.v1._c_errors import raise_mapped_c_error as _raise_mapped_c_error
from app.api.v1._paging import PageQ, PageSizeQ
from app.api.v1._pending import pending
from app.core.errors import (
    CODE_BAD_REQUEST,
    CODE_CONFLICT,
    CODE_DEPENDENCY_NOT_AVAILABLE,
    CODE_FORBIDDEN,
    CODE_NOT_FOUND,
    CODE_UNSUPPORTED_GRAIN,
    AppError,
)
from app.core.permissions import require_admin, require_authenticated, require_csrf
from app.core.providers import (
    get_c_store,
    get_job_store,
    require_data_foundation,
    require_intelligent_analysis,
)
from app.schemas.common import Envelope, envelope
from app.schemas.videos import (
    AnalysisConfig,
    CountsResponse,
    CountPoint,
    ManualValidation,
    Roi,
    ShortForecastRequest,
    ShortForecastResponse,
    VideoAccepted,
    VideoAnalysisAccepted,
    VideoOut,
)
from app.services import e_bridge

router = APIRouter(prefix="/videos", tags=["录像画面人数计数与短时预测"])


@router.get('/capture-folders')
async def capture_folders(request: Request, _session=Depends(require_authenticated)):
    from app.services import capture_library
    return envelope(request, capture_library.folders())


@router.get('/capture-images')
async def capture_images(request: Request, folder: str, _session=Depends(require_authenticated)):
    from app.services import capture_library
    return envelope(request, capture_library.images(folder))


@router.get('/capture-image')
async def capture_image(folder: str, filename: str, _session=Depends(require_authenticated)):
    from app.services import capture_library
    path = capture_library.image(folder, filename)
    return FileResponse(path, media_type=capture_library.MEDIA_TYPES[path.suffix.lower()],
                        headers={'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff'})


@router.get('/{video_id}/counting')
async def current_counting(video_id: str,request: Request,_session=Depends(require_authenticated)):
    from app.services import local_counting
    store=get_c_store()
    if store is None: pending('视频计数','C','data_foundation')
    try:
        if local_counting.is_local(store,video_id):
            return envelope(request,local_counting.current(store,video_id))
        from data_foundation.ingestion.local_video_counts import registered_latest
        return envelope(request,registered_latest(store._store,video_id) if hasattr(store,'_store') else None)
    except Exception as exc:
        _raise_mapped_c_error(exc,'视频计数状态');raise


@router.post('/{video_id}/counting',status_code=202)
async def start_counting(video_id: str,body: AnalysisConfig,request: Request,background_tasks: BackgroundTasks,
    _session=Depends(require_admin),_csrf=Depends(require_csrf),_dep=Depends(require_intelligent_analysis)):
    from app.services import local_counting
    store=get_c_store()
    if store is None: pending('视频计数','C','data_foundation')
    try:
        if local_counting.is_local(store,video_id):
            return envelope(request,local_counting.start(store,video_id,body.model_dump(exclude_none=True)))
        result=e_bridge.analyze_video(video_id,body.model_dump(exclude_none=True))
        def execute():
            from datetime import timezone
            from app.worker import DWorker
            from app.core.config import get_settings
            claimed=store.claim_job('video-counting-button',datetime.now(timezone.utc),lease_seconds=120,
                job_type='video_analysis',job_id=result['job_id'])
            if claimed: DWorker(store,local_counting.counter_for(get_settings().p2pnet_bundle_dir)).run_claimed(claimed)
        background_tasks.add_task(execute)
        return envelope(request,dict(result,status='queued',processed_samples=0,total_samples=0,progress=0))
    except AppError: raise
    except Exception as exc:
        _raise_mapped_c_error(exc,'启动视频计数');raise

# 03-接口总览 §8（27）：grain 仅接受 frame | 5min，默认 5min。
COUNT_GRAINS = ("frame", "5min")

# 03-接口总览 §24 要求「限定格式、大小和时长」，但**没有给出具体数值**，
# 由 D 定并在 README 标注（已回执 C）。时长 D 无法在不引入 ffmpeg 的情况下
# 可靠判定，**不做时长校验**——如实标注，不假装做了。
VIDEO_MAX_BYTES = 2 * 1024 * 1024 * 1024  # 2 GiB
VIDEO_MEDIA_TYPES = {
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".avi": "video/x-msvideo",
    ".mkv": "video/x-matroska",
    ".webm": "video/webm",
}


@router.get("", response_model=Envelope[list[VideoOut]])
async def list_videos(
    request: Request,
    scenic_id: str | None = None,
    camera_id: str | None = None,
    status: str | None = None,
    page: PageQ = 1,
    page_size: PageSizeQ = 20,
    _session=Depends(require_authenticated),
    _dep=Depends(require_data_foundation),
) -> dict:
    """23 GET /videos：录制视频元数据列表；**不返回原始视频内容**。

    2026-10-15 接线：C 0.2.0a2 的 ``uow.video.list_videos`` 已经可用
    （含 scenic/camera 实体类型校验与 status 白名单），不再是「等 C」。
    返回项只含公开列（C 的 ``public_columns``），不含内部存储路径。
    """
    cstore = get_c_store()
    if cstore is None:
        pending("视频列表", "C", "data_foundation")
    try:
        result = getattr(cstore,'list_playback_videos',cstore.list_videos)(
            scenic_id=scenic_id, camera_id=camera_id, status=status,
            page=page, page_size=page_size,
        )
    except Exception as exc:  # noqa: BLE001 —— 统一映射 C 的异常
        _raise_mapped_c_error(exc, "视频列表")
        raise
    items = [VideoOut.model_validate(_video_out(item)) for item in result["items"]]
    # C 的返回是 ``{"items": [...], "page": int, "page_size": int, "total": int}``
    # （page / page_size 是普通整数，不是 Page 对象）
    return envelope(
        request,
        items,
        page=result["page"],
        page_size=result["page_size"],
        total=result["total"],
    )


def _video_out(row: dict) -> dict:
    """C 的录像公开列 → D/B 的 ``VideoOut`` 形状（时间统一带 +08:00）。"""
    from app.services.e_bridge import TZ_CN

    def _iso(value):
        if value is None:
            return None
        if getattr(value, "utcoffset", None) is None:
            return value.isoformat()
        return value.astimezone(TZ_CN).isoformat()

    return {
        "id": row.get("id"),
        "scenic_id": row.get("scenic_id"),
        "camera_id": row.get("camera_id"),
        "recorded_start_at": _iso(row.get("recorded_start_at")),
        "recorded_end_at": _iso(row.get("recorded_end_at")),
        "status": row.get("status"),
        "permission_record_id": row.get("permission_record_id"),
        **{key:row[key] for key in ('title','origin','duration_seconds','file_digest','source_note') if key in row},
    }


@router.post("", status_code=201, response_model=VideoAccepted)
async def upload_video(
    file: UploadFile = File(description=f"录像文件（{', '.join(VIDEO_MEDIA_TYPES)}，≤2GiB）"),
    scenic_id: str = Form(min_length=1, max_length=64),
    camera_id: str = Form(min_length=1, max_length=64),
    recorded_start_at: str = Form(min_length=1, max_length=40),
    recorded_end_at: str = Form(min_length=1, max_length=40),
    permission_record_id: str = Form(min_length=1, max_length=64),
    camera_location_note: str | None = Form(default=None, max_length=500),
    _admin=Depends(require_admin),
    _csrf=Depends(require_csrf),
    _dep=Depends(require_data_foundation),
) -> VideoAccepted:
    """24 POST /videos：管理员；multipart 上传；授权记录无效时拒绝（P2）。

    2026-10-17 接线：C 0.2.0a2 的 ``uow.video.register_video`` /
    ``uow.provenance.register_asset`` / ``DataStore.persist_file`` 三个入口**早就齐了**，
    之前一直挂 503 是 D 的判断滞后（与 23/25、05—16 同类）。

    D 只负责**形状**：格式与大小白名单、时间可解析且 ``end > start``、流式落盘。
    **「能不能传」由 C 判定**——授权是否有效、景区与摄像头是否父子关系、
    原件/授权/录像三者来源是否一致，全在 C 的 ``register_video`` 里
    （``InvalidArgument`` / ``InvalidPermission``），D 不重新实现。

    ⚠️ C 的 ``videos`` 表有 ``CHECK (recorded_end_at > recorded_start_at)``，
    违反会走数据库 IntegrityError（不是 C 的异常族，会变成 500）。
    所以 **D 必须提前校验时间顺序**，不能指望 C 兜住。
    """
    name = file.filename or ""
    suffix = Path(name).suffix.lower()
    if suffix not in VIDEO_MEDIA_TYPES:
        raise AppError(400, CODE_BAD_REQUEST, f"不支持的录像格式：{suffix or '(无扩展名)'}")

    # SpooledTemporaryFile 支持 seek/tell，不把整段录像读进内存
    stream = file.file
    stream.seek(0, 2)
    size = stream.tell()
    stream.seek(0)
    if size > VIDEO_MAX_BYTES:
        raise AppError(400, CODE_BAD_REQUEST, "录像超过大小上限（2GiB）")

    start = _parse_upload_time(recorded_start_at, "recorded_start_at")
    end = _parse_upload_time(recorded_end_at, "recorded_end_at")
    if end <= start:
        raise AppError(400, CODE_BAD_REQUEST, "recorded_end_at 必须晚于 recorded_start_at")

    cstore = get_c_store()
    if cstore is None:
        pending("视频上传", "C", "data_foundation")

    # ① 先取授权：拿到 source_id（落盘路径要用），并让无效授权在落盘前就拒绝
    try:
        permission = cstore.get_permission(permission_record_id)
    except Exception as exc:  # noqa: BLE001
        _raise_mapped_c_error(exc, "录像授权")
        raise
    source_id = permission.get("source_id")
    if not source_id:
        raise AppError(400, CODE_BAD_REQUEST, "授权记录缺少 source_id")

    # ② 落盘（内容寻址：raw/<source_id>/<sha256>/original<suffix>，重复内容复用）
    try:
        stored = getattr(cstore,'persist_video_file',cstore.persist_file)(
            source_id=source_id, original_name=name, stream=stream,
        )
    except Exception as exc:  # noqa: BLE001
        _raise_mapped_c_error(exc, "录像落盘")
        raise

    # ③ 原件登记 + 录像登记（同一 C 写事务，避免留下没有录像指向的孤儿原件）
    try:
        video = cstore.register_uploaded_video(
            source_id=source_id, scenic_id=scenic_id, camera_id=camera_id,
            permission_record_id=permission_record_id,
            recorded_start_at=start, recorded_end_at=end,
            camera_location_note=camera_location_note,
            relative_path=stored["relative_path"], sha256=stored["sha256"],
            byte_size=stored["byte_size"], media_type=VIDEO_MEDIA_TYPES[suffix],
            original_name=name,
        )
    except Exception as exc:  # noqa: BLE001
        _raise_mapped_c_error(exc, "录像登记")
        raise

    return VideoAccepted(
        video_id=str(video.get("id")),
        status=str(video.get("status", "uploaded")),
        file_digest=stored["sha256"],
    )


def _parse_upload_time(value: str, field: str) -> datetime:
    """表单时间串 → tz-aware datetime。naive 按 +08:00（业务在中国）。

    C 的 ``register_video`` 内部会 ``utc_naive()`` 转 UTC 存库，D 只需保证
    传进去的是 aware；解析不出来一律 400（不静默按 now 处理）。
    """
    from app.services.e_bridge import TZ_CN

    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        raise AppError(400, CODE_BAD_REQUEST, f"{field} 不是合法的 ISO 时间：{value}") from None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=TZ_CN)
    return parsed


@router.get("/{video_id}/playback")
async def playback(
    video_id: str,
    request: Request,
    _session=Depends(require_authenticated),
    _dep=Depends(require_data_foundation),
):
    """25 GET /videos/{video_id}/playback：视频流，支持 Range；禁止共享缓存。

    2026-10-15 接线：C 0.2.0a2 的 ``uow.video.get_playback_ref`` 已经提供了
    「授权核验 + DATA_ROOT 越界检查 + 文件存在 + SHA256/大小比对」的完整取回
    入口，D 不再把它当「等 C」，只负责流式响应本身：

    - **Range**：解析 ``bytes=start-end``，回 206 + ``Content-Range``；
      非法/越界 Range → 416 + ``Content-Range: bytes */<size>``；
    - **禁止共享缓存**：``Cache-Control: no-store, private``
      （03 §25 明确要求，录像不进任何共享缓存）；
    - **不泄露路径**：响应体只有字节流，头部不含服务器路径或资产 ID。
    """
    cstore = get_c_store()
    if cstore is None:
        pending("录像回放", "C", "data_foundation")
    try:
        ref = cstore.get_playback_ref(video_id)
    except Exception as exc:  # noqa: BLE001 —— 统一映射 C 的异常
        _raise_mapped_c_error(exc, "录像回放")
        raise

    path = Path(ref["path"])
    size = int(ref["byte_size"])
    media_type = ref.get("media_type") or "video/mp4"
    headers = {
        "Accept-Ranges": "bytes",
        # 03 §25：禁止共享缓存（录像属获准内容，不得进 CDN / 代理缓存）
        "Cache-Control": "no-store, private",
        "X-Content-Type-Options": "nosniff",
    }

    range_header = request.headers.get("range")
    start, end = 0, size - 1
    status_code = 200
    if range_header:
        parsed = _parse_range(range_header, size)
        if parsed is None:
            return _range_not_satisfiable(size, headers)
        start, end = parsed
        status_code = 206
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    length = max(0, end - start + 1)
    headers["Content-Length"] = str(length)

    async def _iterfile():
        stream = await anyio.open_file(path, "rb")
        try:
            await stream.seek(start)
            remaining = length
            while remaining > 0:
                chunk = await stream.read(min(1 << 20, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk
        finally:
            # Browser metadata probes abort early; always release the Windows file handle.
            with anyio.CancelScope(shield=True):
                await stream.aclose()

    return StreamingResponse(
        _iterfile(), status_code=status_code, media_type=media_type, headers=headers,
    )


def _parse_range(range_header: str, size: int) -> tuple[int, int] | None:
    """解析单个字节区间；不支持多区间（返回 None → 416）。

    只接受 ``bytes=<start>-<end>``（end 可省）。``start`` 必须在 [0, size)，
    ``end`` 缺省或越界时截断到 ``size - 1``；``start > end`` 视为非法。
    """
    import re

    match = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header.strip())
    if match is None:
        return None
    raw_start, raw_end = match.group(1), match.group(2)
    if raw_start == "" and raw_end == "":
        return None
    if raw_start == "":  # bytes=-N：最后 N 字节
        suffix = int(raw_end)
        if suffix <= 0:
            return None
        return (max(0, size - suffix), size - 1)
    start = int(raw_start)
    end = int(raw_end) if raw_end else size - 1
    if start >= size or start > end:
        return None
    return (start, min(end, size - 1))


def _range_not_satisfiable(size: int, headers: dict):
    from fastapi.responses import Response

    response_headers = {**headers, "Content-Range": f"bytes */{size}"}
    return Response(status_code=416, headers=response_headers)


@router.post("/{video_id}/analyses", status_code=202, response_model=Envelope[VideoAnalysisAccepted])
async def create_analysis(
    video_id: str,
    body: AnalysisConfig,
    request: Request,
    _admin=Depends(require_admin),
    _csrf=Depends(require_csrf),
    _dep=Depends(require_intelligent_analysis),
) -> dict:
    """26 POST /videos/{video_id}/analyses：管理员；登记 P2PNet 画面人数分析作业。

    请求含权重版本、ROI、抽帧间隔、5 分钟聚合与推理运行时/设备
    （E 0.6.0 只接受 count_model=p2pnet、inference_engine=pytorch、device=CPU）；
    E 侧经 D 的 submit 绑定真实入队并返回 analysis_id，真正的帧处理由
    worker 领 video_analysis 作业执行。透传配置不带 None 字段
    （pytorch 不得携带 openvino_version=null，E 会拒收）。
    """
    result = e_bridge.analyze_video(video_id, body.model_dump(exclude_none=True))
    return envelope(
        request,
        VideoAnalysisAccepted(
            job_id=str(result["job_id"]),
            analysis_id=str(result["analysis_id"]),
            status=result["status"],
            video_id=result["video_id"],
            inference_engine=body.inference_engine,
        ).model_dump(mode="json"),
    )


@router.get("/{video_id}/counts", response_model=Envelope[CountsResponse])
async def get_counts(
    video_id: str,
    request: Request,
    analysis_id: str,
    start: str | None = None,
    end: str | None = None,
    grain: str = "5min",
    _session=Depends(require_authenticated),
    _dep=Depends(require_intelligent_analysis),
) -> dict:
    """27 GET /videos/{video_id}/counts：画面人数序列（frame | 5min）。

    未完成返回 409 analysis_not_ready；`value` 是画面区域估计可见人数，
    不是该时间窗的累计人次。不受支持的粒度返回 422 unsupported_grain。
    """
    if grain not in COUNT_GRAINS:
        raise AppError(
            422,
            CODE_UNSUPPORTED_GRAIN,
            "不支持的计数粒度",
            {"grain": grain, "supported": list(COUNT_GRAINS)},
        )
    from app.services import local_counting
    store=get_c_store()
    if store and local_counting.is_local(store,video_id):
        try:
            result=local_counting.counts(store,video_id,analysis_id,grain)
            return envelope(request,CountsResponse.model_validate(result).model_dump(mode='json'))
        except Exception as exc:
            _raise_mapped_c_error(exc,'视频计数结果');raise
    result = e_bridge.get_camera_counts(video_id, analysis_id, start, end, grain)
    points = [CountPoint.model_validate(p) for p in result.get("points", [])]
    manual = result.get("manual_validation")
    roi = result.get("roi")
    # 分析元数据按前端（B）约定放顶层；兼容 C 仍在 analysis 子对象里的返回。
    meta = result.get("analysis") or {}

    def _meta(key: str):
        return result.get(key, meta.get(key))

    # 审计摘要（weights_sha256 / model_source_revision）**不能经 E**
    # （E 的 CountsResponse 是 extra="forbid"，这两个键既不能放顶层也不能进
    # VideoAnalysisConfig）。2026-10-17 起由 D 从 **C 的
    # visible_analyses.provenance** 读——D 私表 d_video_analyses 已不再写入。
    audit: dict = {k: result.get(k) for k in ("weights_sha256", "model_source_revision")}

    return envelope(
        request,
        CountsResponse(
            analysis_id=str(result["analysis_id"]),
            roi=Roi.model_validate(roi) if roi else None,
            grain=grain,
            points=points,
            count_model=_meta("count_model"),
            weights_version=_meta("weights_version"),
            weights_sha256=audit.get("weights_sha256"),
            model_source_revision=audit.get("model_source_revision"),
            confidence_threshold=_meta("confidence_threshold"),
            inference_engine=_meta("inference_engine"),
            device=_meta("device"),
            openvino_version=_meta("openvino_version"),
            sample_interval_seconds=_meta("sample_interval_seconds"),
            manual_validation=ManualValidation.model_validate(manual) if manual else None,
        ).model_dump(mode="json"),
    )


@router.post("/{video_id}/short-forecast", response_model=Envelope[ShortForecastResponse])
async def short_forecast(
    video_id: str,
    body: ShortForecastRequest,
    request: Request,
    _session=Depends(require_csrf),
    _dep=Depends(require_intelligent_analysis),
) -> dict:
    """28 POST /videos/{video_id}/short-forecast：只用 cutoff_at 之前的有效计数做预测。

    预测指标固定 `camera_visible_people`，不接受进出方向参数。

    2026-10-17 起预测结果由 **C 的 ``visible_forecasts``** 保管（挂在独立
    ``visible_forecast`` 作业上），这条 HTTP 当场创建并消费该作业，再把 C 里
    落好的那条读回来返回——**响应与 C 的存储同源**，不是把 E 的返回值另加工一遍。
    """
    result = e_bridge.forecast_short_term(
        video_id,
        body.analysis_id,
        body.cutoff_at,
        body.horizon_minutes,
        body.step_minutes,
    )
    normalised = e_bridge.normalise_short_forecast(result)
    return envelope(request, ShortForecastResponse.model_validate(normalised).model_dump(mode="json"))
