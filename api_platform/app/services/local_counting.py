"""Run E's P2PNet on directory recordings; C owns task and count persistence."""
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from threading import Lock
import logging,math
from pathlib import Path
from datetime import datetime,timezone
from data_foundation.ingestion import local_video_counts as db,local_videos
from app.core.config import get_settings
from app.core.errors import AppError
from app.services import counting_captures

_executor=ThreadPoolExecutor(max_workers=1,thread_name_prefix='p2pnet-video')
_scheduled=set();_lock=Lock()
logger=logging.getLogger(__name__)

def native(store): return store._store
def is_local(store,video_id): return hasattr(store,'_store') and bool(local_videos.get_file(native(store),video_id))

def public(row):
    if not row: return None
    return {'analysis_id':row['id'],'status':row['status'],'processed_samples':row['processed_samples'],
        'total_samples':row['total_samples'],'progress':round(100*row['processed_samples']/max(1,row['total_samples']),1),
        'error_message':row['error_message'],'sample_interval_seconds':row['config']['sample_interval_seconds'],
        'time_basis':'relative','duration_seconds':row['duration_seconds']}

def start(store,video_id,config):
    from intelligent_analysis.contracts import VideoAnalysisConfig
    from pydantic import ValidationError
    local_videos.sync_directory(native(store),get_settings().video_storage_dir)
    video=local_videos.get_file(native(store),video_id)
    if not video: raise AppError(404,'not_found','录像不存在')
    # Let E validate geometry and the fixed model contract, without loading weights in the HTTP thread.
    internal=dict(config);internal['roi']={'points':[{'x':x,'y':y} for x,y in config['roi']['points']]}
    try: VideoAnalysisConfig.model_validate(internal)
    except ValidationError as exc: raise AppError(422,'bad_request','计数参数或统计区域不符合模型要求') from exc
    from app.services.e_bindings import get_p2p_counter
    counter=get_p2p_counter()
    if counter is None: raise AppError(503,'dependency_not_available','P2PNet 模型尚未配置')
    if config['device']!='CPU' or config['inference_engine']!='pytorch' or config['weights_version']!=counter.provenance['weights_version']:
        raise AppError(422,'unsupported','当前只支持已配置的 P2PNet 权重和 PyTorch CPU')
    store.get_playback_ref(video_id)
    row=db.request(native(store),video,config)
    if row['status']=='ready' and not counting_captures.complete(row):
        row=db.requeue_for_captures(native(store),row['id'])
    schedule(store,row)
    return public(row)

def current(store,video_id):
    local_videos.sync_directory(native(store),get_settings().video_storage_dir)
    row=db.latest(native(store),video_id)
    if row and row['status']=='queued': schedule(store,row)
    return public(row)

def schedule(store,row):
    if row['status']!='queued': return
    key=row['id']
    with _lock:
        if key in _scheduled: return
        _scheduled.add(key)
    def work():
        try: run(store,key)
        finally:
            with _lock: _scheduled.discard(key)
    try: _executor.submit(work)
    except Exception:
        with _lock: _scheduled.discard(key)
        raise

@lru_cache(maxsize=1)
def counter_for(bundle):
    import torch
    from intelligent_analysis.vision.p2p_counter import P2PNetCounter
    torch.set_num_threads(4)
    return P2PNetCounter.from_team_bundle(bundle)

def run(store,analysis_id):
    if not db.claim(native(store),analysis_id): return
    try:
        import cv2
        from intelligent_analysis.contracts import VideoAnalysisConfig
        from intelligent_analysis.vision.benchmark import point_count
        row=db.get(native(store),analysis_id);config=dict(row['config'])
        config['roi']={'points':[{'x':x,'y':y} for x,y in config['roi']['points']]}
        parsed=VideoAnalysisConfig.model_validate(config)
        counter=counter_for(get_settings().p2pnet_bundle_dir)
        counter.validate_config(parsed.model_dump(mode='json',exclude_none=True))
        ref=store.get_playback_ref(row['video_id'])
        if local_videos.digest(Path(ref['path']))!=row['video_sha256']:
            raise ValueError('Video changed before analysis')
        capture=cv2.VideoCapture(ref['path']);samples=[]
        try:
            fps=capture.get(cv2.CAP_PROP_FPS)
            if not capture.isOpened() or not math.isfinite(fps) or fps<=0: raise ValueError('Video cannot be decoded')
            for index in range(row['total_samples']):
                requested=index*parsed.sample_interval_seconds
                frame_index=round(requested*fps)
                offset=frame_index/fps
                if offset>=row['duration_seconds']: break
                if not capture.set(cv2.CAP_PROP_POS_FRAMES,frame_index): raise ValueError('Cannot seek video sample')
                ok,frame=capture.read()
                if not ok: raise ValueError('Cannot decode video sample')
                predictions=counter(cv2.cvtColor(frame,cv2.COLOR_BGR2RGB))
                value=point_count(predictions,parsed.roi,parsed.confidence_threshold)
                counting_captures.save(frame,points=predictions,roi=parsed.roi,threshold=parsed.confidence_threshold,
                    value=value,video_id=row['video_id'],analysis_id=analysis_id,index=index,offset=offset)
                samples.append((offset,value));db.progress(native(store),analysis_id,len(samples))
        finally: capture.release()
        # Recheck content before committing; replaced files never inherit old results.
        store.get_playback_ref(row['video_id'])
        if local_videos.digest(Path(ref['path']))!=row['video_sha256']: raise ValueError('Video changed during analysis')
        counting_captures.finish(row,len(samples))
        db.finish(native(store),analysis_id,samples,counter.provenance)
    except Exception:
        logger.exception('P2PNet directory analysis failed: %s',analysis_id)
        db.fail(native(store),analysis_id,'计数未完成，请检查视频、模型配置及 cap_images 目录写入权限和剩余空间后重试。')

def counts(store,video_id,analysis_id,grain):
    local_videos.sync_directory(native(store),get_settings().video_storage_dir)
    row,samples=db.result(native(store),video_id,analysis_id,grain)
    provenance=row['provenance'] or {};config=row['config']
    return {'analysis_id':analysis_id,'metric':'camera_visible_people','grain':grain,'roi':config['roi'],
        'time_basis':'relative','duration_seconds':row['duration_seconds'],'video_sha256':row['video_sha256'],
        'points':[{'metric':'camera_visible_people','value':p['value'],'unit':'人','grain':p['grain'],
            'offset_seconds':p['offset_seconds'],'period_start_seconds':p['period_start_seconds'],
            'quality_status':p['quality_status'],'sample_count':p['sample_count'],
            'aggregation_rule':'median' if grain=='5min' else None,'source_ids':[video_id]} for p in samples],
        'count_model':'p2pnet','weights_version':provenance.get('weights_version'),
        'weights_sha256':provenance.get('weights_sha256'),'model_source_revision':provenance.get('model_source_revision'),
        'confidence_threshold':provenance.get('confidence_threshold'),'inference_engine':'pytorch','device':'CPU',
        'sample_interval_seconds':config['sample_interval_seconds'],'manual_validation':None}
