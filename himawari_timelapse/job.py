"""Daily durable checkpoints, in-memory frames and validated publication."""
from dataclasses import asdict
from itertools import groupby
from pathlib import Path
import concurrent.futures
import hashlib
import json
import shutil
import subprocess
import time
from . import core, daynight
from .config import estimate

def atomic_json(path, value):
    path=Path(path)
    temporary=path.with_suffix(path.suffix+'.tmp')
    temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
    temporary.replace(path)

def sha256(path):
    digest=hashlib.sha256()
    with Path(path).open('rb') as source:
        for chunk in iter(lambda: source.read(2**20),b''):digest.update(chunk)
    return digest.hexdigest()

def validate(path, config, frames):
    report=core.inspect_video(path)
    stream=report['streams'][0]
    if (int(stream['nb_read_frames']),stream['width'],stream['height'],stream['avg_frame_rate']) != (frames,config.width,config.height,f'{config.fps}/1'):
        raise ValueError('视频帧数、尺寸或帧率不符')
    if abs(float(report['format']['duration'])-frames/config.fps)>.1:
        raise ValueError('视频时长不符')
    return report

def configure_source(config):
    daynight.POOL.shutdown(wait=True)
    daynight.POOL=concurrent.futures.ThreadPoolExecutor(max_workers=config.workers)

def render(times, path, config, loader=None):
    return core.stream_segment(times,path,config.fps,loader=loader or daynight.observation,
        width=config.width,height=config.height,tz=config.tz,crf=config.crf,preset=config.preset)

def run(config, loader=None):
    config.validate()
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        raise RuntimeError('请先安装 FFmpeg 和 ffprobe 并加入 PATH')
    destination=Path(config.save_root)/('run_'+config.key)
    scratch=Path(config.scratch)/config.key
    destination.mkdir(parents=True,exist_ok=True);scratch.mkdir(parents=True,exist_ok=True)
    required=estimate(config)['scratch_peak_mib']*2**20
    if shutil.disk_usage(scratch).free < required:
        raise OSError('工作盘剩余空间不足，调整 scratch 或减少日期范围')
    atomic_json(destination/'config.json',asdict(config))
    status=destination/'status.json'
    daynight.GAP_LOG=destination/'data_gaps.jsonl'
    Path(daynight.GAP_LOG).touch(exist_ok=True)
    configure_source(config)
    atomic_json(status,dict(phase='rendering',updated_unix=time.time(),config_key=config.key,frames=config.frames))
    segments=[]
    try:
        for date, values in groupby(config.times(),key=lambda t:t.astimezone(config.tz).strftime('%Y%m%d')):
            times=list(values);saved=destination/(date+'.mp4');checkpoint=destination/(date+'.json')
            reusable=False
            if saved.exists() and checkpoint.exists():
                record=json.loads(checkpoint.read_text())
                reusable=(record.get('config_key')==config.key and record.get('frames')==len(times)
                    and record.get('sha256')==sha256(saved))
            if reusable:
                print('CHECKPOINT verified; skip',date,flush=True)
            else:
                print('DAY',date,len(times),'frames',flush=True)
                local=scratch/(date+'.mp4')
                began=time.monotonic();render(times,local,config,loader)
                report=validate(local,config,len(times))
                pending=saved.with_suffix('.mp4.uploading')
                shutil.copyfile(local,pending);pending.replace(saved)
                atomic_json(checkpoint,dict(config_key=config.key,frames=len(times),sha256=sha256(saved),
                    video_bytes=saved.stat().st_size,validation=report,elapsed_seconds=time.monotonic()-began))
                local.unlink()
            atomic_json(status,dict(phase='rendering',completed_day=date,updated_unix=time.time(),config_key=config.key))
            segments.append(saved)
        atomic_json(status,dict(phase='concatenating',updated_unix=time.time(),config_key=config.key))
        copies=[]
        for saved in segments:
            local=scratch/saved.name;shutil.copyfile(saved,local);copies.append(local)
        # Relative generated names avoid escaping user paths in FFmpeg concat syntax.
        concat=scratch/'concat.txt'
        concat.write_text(''.join(f"file '{p.name}'\n" for p in copies),encoding='utf-8')
        local_final=scratch/'earth_timelapse.mp4'
        subprocess.run(['ffmpeg','-v','error','-y','-f','concat','-safe','1','-i',str(concat.resolve()),
                        '-c','copy','-movflags','+faststart',str(local_final)],check=True)
        report=validate(local_final,config,config.frames)
        final=destination/'earth_timelapse.mp4'
        pending=destination/'earth_timelapse.mp4.uploading';shutil.copyfile(local_final,pending);pending.replace(final)
        manifest=dict(config_key=config.key,path=str(final.resolve()),bytes=final.stat().st_size,sha256=sha256(final),validation=report)
        atomic_json(destination/'manifest.json',manifest)
        atomic_json(status,dict(phase='complete',updated_unix=time.time(),**manifest))
        for path in copies+[local_final]:path.unlink()
        print('COMPLETE',final,flush=True)
        return final
    except BaseException as error:
        atomic_json(status,dict(phase='failed',error=str(error),updated_unix=time.time(),config_key=config.key))
        raise

def benchmark(config, frames=12):
    if not 2<=frames<=144:
        raise ValueError('基准帧数范围 2–144')
    workspace=Path(config.workspace);workspace.mkdir(parents=True,exist_ok=True)
    configure_source(config)
    times=list(config.times())[:frames]
    if len(times)<2:raise ValueError('日期范围至少需要两个样本')
    path=workspace/'benchmark.mp4'
    daynight.GAP_LOG=workspace/'benchmark_data_gaps.jsonl'
    began=time.monotonic();render(times,path,config)
    elapsed=time.monotonic()-began
    calibration=dict(config_key=config.key,frames=len(times),seconds_per_frame=elapsed/len(times),
        bitrate_mbps=path.stat().st_size*8/(len(times)/config.fps)/1e6,measured_unix=time.time())
    atomic_json(workspace/'calibration.json',calibration)
    return calibration
