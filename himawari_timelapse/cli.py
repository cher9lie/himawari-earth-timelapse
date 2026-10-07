"""Interactive setup, estimation, local execution and Colab orchestration."""
import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile
from .config import Config, estimate

def show_estimate(config):
    path=Path(config.workspace)/'calibration.json'
    calibration=json.loads(path.read_text()) if path.exists() else None
    if calibration and calibration.get('config_key')!=config.key:calibration=None
    print(json.dumps(estimate(config,calibration),ensure_ascii=False,indent=2))

def wizard(path):
    config=Config.load(path) if Path(path).exists() else Config()
    fields=[('start','开始日期 YYYY-MM-DD'),('end','结束日期（不包含）'),
            ('interval_minutes','采样间隔（分钟，10的整数倍）'),('fps','播放帧率'),
            ('width','宽度'),('height','高度'),('timezone_offset','UTC时差（北京时间为8）'),
            ('workspace','本机工作目录'),('download_dir','成片下载目录'),
            ('save_root','云端/本地分段持久化目录'),('scratch','渲染临时工作目录'),
            ('session','专用 Colab 会话名'),('output_name','成片文件名')]
    for name,label in fields:
        old=getattr(config,name)
        value=input(f'{label} [{old}]: ').strip()
        if value:setattr(config,name,type(old)(value))
    config.validate().save(path)
    print('已保存',Path(path).resolve());show_estimate(config)

def colab(config, *args, capture=False, check=True):
    command=[sys.executable,'-m','himawari_timelapse.colab_bridge',*args]
    # No shell interpolation and no embedded proxy. Requests honors user's environment.
    return subprocess.run(command,check=check,text=True,encoding='utf-8',
                          capture_output=capture)

def archive_package(workspace):
    package=Path(__file__).parent
    path=workspace/'package.zip'
    with zipfile.ZipFile(path,'w',zipfile.ZIP_DEFLATED) as archive:
        for source in package.glob('*.py'):
            archive.write(source,'himawari_timelapse/'+source.name)
    return path

def remote_exec(config, script, workspace, timeout=120):
    path=workspace/'remote_operation.py'
    path.write_text(script,encoding='utf-8')
    colab(config,'exec','-s',config.session,'--timeout',str(timeout),'-f',str(path))

def collect(config,workspace):
    from .transfer import download_verified
    remote_dir=config.save_root.rstrip('/')+'/run_'+config.key
    status_path=workspace/'retrieval_status.json'
    colab(config,'download','-s',config.session,remote_dir+'/status.json',str(status_path))
    if json.loads(status_path.read_text()).get('phase')!='complete':
        raise RuntimeError('云端制作尚未完成；取回不会向运行中的生产提交 exec')
    if shutil.disk_usage(workspace).free < 96*2**20:
        raise OSError('本机工作盘至少需要96 MiB空闲空间用于下载中转')
    manifest_path=workspace/'manifest.json'
    colab(config,'download','-s',config.session,remote_dir+'/manifest.json',str(manifest_path))
    manifest=json.loads(manifest_path.read_text())
    if manifest['config_key']!=config.key:raise ValueError('云端任务配置不符')
    source=remote_dir+'/earth_timelapse.mp4'
    def fetch_chunk(offset,length):
        script=f'''import pathlib, hashlib, json
source=pathlib.Path({source!r})
with source.open('rb') as f:
    f.seek({offset});data=f.read({length})
assert len(data)=={length}
pathlib.Path('/content/earth_transfer.bin').write_bytes(data)
pathlib.Path('/content/earth_transfer.json').write_text(json.dumps({{'sha256':hashlib.sha256(data).hexdigest()}}))
'''
        remote_exec(config,script,workspace)
        chunk=workspace/'incoming.bin';record=workspace/'incoming.json'
        colab(config,'download','-s',config.session,'/content/earth_transfer.bin',str(chunk))
        colab(config,'download','-s',config.session,'/content/earth_transfer.json',str(record))
        return chunk,json.loads(record.read_text())['sha256']
    final=download_verified(manifest,Path(config.download_dir)/config.output_name,fetch_chunk)
    for name in ['data_gaps.jsonl','config.json']:
        colab(config,'download','-s',config.session,remote_dir+'/'+name,str(final.parent/(final.stem+'.'+name)))
    if shutil.which('ffprobe'):
        from .job import validate
        validate(final,config,config.frames)
    print('VERIFIED',final.resolve(),flush=True)
    colab(config,'stop','-s',config.session)

def cloud(config,action):
    workspace=Path(config.workspace).resolve();workspace.mkdir(parents=True,exist_ok=True)
    status=colab(config,'status','-s',config.session,capture=True)
    created='not found' in status.stdout
    if created:
        colab(config,'new','-s',config.session)
        colab(config,'drivemount','-s',config.session)
    else:
        print(status.stdout)
    if action=='retrieve':
        collect(config,workspace);return
    if action in ['run','benchmark']:
        # Check for an active/complete existing job via file API before submitting code.
        remote_dir=config.save_root.rstrip('/')+'/run_'+config.key
        existing=colab(config,'ls','-s',config.session,remote_dir,capture=True,check=False)
        if existing.returncode==0:
            state_path=workspace/'cloud_status.json'
            colab(config,'download','-s',config.session,remote_dir+'/status.json',str(state_path))
            state=json.loads(state_path.read_text())
            if state['phase']=='complete' and action=='run':collect(config,workspace);return
            if state['phase'] not in ['failed','complete'] and not created:
                raise RuntimeError('云端任务仍标记为运行中。请检查 Colab 云端日志；不会提交第二条 exec。确认原运行时已结束后，用新会话名恢复。')
    package=archive_package(workspace)
    colab(config,'upload','-s',config.session,str(package),'/content/earth_package.zip')
    payload=asdict(config);payload['workspace']='/content/earth_benchmark'
    setup=f'''import pathlib, zipfile, sys, subprocess, shutil
with zipfile.ZipFile('/content/earth_package.zip') as z:z.extractall('/content/earth_tool')
sys.path.insert(0,'/content/earth_tool')
subprocess.check_call([sys.executable,'-m','pip','-q','install','Pillow>=10,<13','numpy>=1.26,<3','requests>=2.31,<3'])
if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
    subprocess.check_call(['apt-get','-qq','update'])
    subprocess.check_call(['apt-get','-qq','install','-y','ffmpeg'])
from himawari_timelapse.config import Config
from himawari_timelapse.job import run, benchmark
config=Config(**{payload!r}).validate()
'''
    if action=='benchmark':
        remote_exec(config,setup+'benchmark(config)\n',workspace,timeout=1800)
        colab(config,'download','-s',config.session,'/content/earth_benchmark/calibration.json',str(workspace/'calibration.json'))
        show_estimate(config)
        print('基准完成；运行时保留用于下一步制作。结束使用请运行 release。')
    else:
        # Single production exec; on connection failure preserve cloud job/Drive checkpoints.
        remote_exec(config,setup+'run(config)\n',workspace,timeout=86400)
        collect(config,workspace)

def main():
    parser=argparse.ArgumentParser(description='向日葵昼夜延时视频：配置、估算、按日恢复与校验下载')
    parser.add_argument('command',choices=['configure','estimate','doctor','benchmark','run','retrieve','release'])
    parser.add_argument('--config',default='config.json')
    parser.add_argument('--backend',choices=['local','colab'],default='colab')
    args=parser.parse_args()
    try:
        if args.command=='configure':wizard(args.config);return
        if args.command=='doctor':
            print('Python:',sys.version.split()[0]);print('FFmpeg:',shutil.which('ffmpeg'));print('ffprobe:',shutil.which('ffprobe'))
            from importlib.metadata import version, PackageNotFoundError
            for name in ['Pillow','numpy','requests','google-colab-cli']:
                try:print(name,version(name))
                except PackageNotFoundError:print(name,'not installed')
            return
        config=Config.load(args.config)
        if args.command=='estimate':show_estimate(config);return
        if args.command=='release':colab(config,'stop','-s',config.session);return
        if args.backend=='colab':cloud(config,args.command);return
        from .job import run, benchmark, atomic_json, sha256
        if args.command=='benchmark':benchmark(config);show_estimate(config)
        elif args.command=='run':
            source=run(config)
            from .transfer import download_verified
            manifest=json.loads((source.parent/'manifest.json').read_text())
            def fetch(offset,length):
                chunk=Path(config.workspace)/'incoming.bin';chunk.parent.mkdir(parents=True,exist_ok=True)
                with source.open('rb') as original:
                    original.seek(offset);chunk.write_bytes(original.read(length))
                return chunk,sha256(chunk)
            final=download_verified(manifest,Path(config.download_dir)/config.output_name,fetch)
            shutil.copyfile(source.parent/'data_gaps.jsonl',final.with_suffix('.data_gaps.jsonl'))
            print('VERIFIED',final.resolve())
        else:raise ValueError('本地后端请使用 run；retrieve/release 只适用于 Colab')
    except (ValueError,OSError,RuntimeError,subprocess.CalledProcessError) as error:
        print('ERROR:',error,file=sys.stderr)
        print('已完成分段与续传文件保留；检查问题后用相同配置恢复。',file=sys.stderr)
        raise SystemExit(1)

if __name__=='__main__':main()
