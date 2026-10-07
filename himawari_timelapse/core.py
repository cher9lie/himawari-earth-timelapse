import datetime as dt
import pathlib, urllib.request, urllib.error, io, json, subprocess, shutil, time, hashlib, os, select
from PIL import Image, ImageDraw, ImageFont

TZ=dt.timezone(dt.timedelta(hours=8))
BASE='https://himawari8-dl.nict.go.jp/himawari8/img/D531106'

def fetch(url):
    for attempt in range(4):
        try:
            with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'EarthTimelapsePersonal/1.0'}),timeout=30) as response:
                return response.read()
        except urllib.error.HTTPError as error:
            if error.code==404:raise RuntimeError('影像不存在：'+url) from error
            if attempt==3:raise
        except (OSError,TimeoutError):
            if attempt==3:raise
        time.sleep(2**attempt)

def observation(timestamp):
    globe=Image.new('RGB',(1100,1100),'black')
    for y in range(2):
        for x in range(2):
            url=f'{BASE}/2d/550/{timestamp:%Y/%m/%d/%H%M%S}_{x}_{y}.png'
            with Image.open(io.BytesIO(fetch(url))) as tile:
                tile.load()
                if tile.size!=(550,550):raise RuntimeError('瓦片尺寸异常：'+url)
                globe.paste(tile.convert('RGB'),(550*x,550*y))
    return globe

def pick_font(size):
    for path in ['/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',r'C:\Windows\Fonts\arial.ttf']:
        try:return ImageFont.truetype(path,size)
        except OSError:pass
    return ImageFont.load_default()

def video_frame(timestamp,globe,width=1920,height=1080,tz=TZ):
    held=globe.info.get('held_frame',False)
    if held:timestamp=dt.datetime.fromisoformat(globe.info['capture_time'])
    frame=Image.new('RGB',(width,height),'black')
    scale=min(width/1920,height/1080)
    globe_size=max(2,int(1000*scale))
    frame.paste(globe.resize((globe_size,globe_size),Image.Resampling.LANCZOS),((width-globe_size)//2,(height-globe_size)//2))
    draw=ImageDraw.Draw(frame)
    margin=max(8,int(40*scale))
    draw.text((margin,margin),'HIMAWARI | DAY + B13 NIGHT',font=pick_font(max(10,int(26*scale))),fill='#bbbbbb')
    draw.text((margin,height-max(38,int(90*scale))),timestamp.astimezone(tz).strftime('%Y-%m-%d %H:%M'),font=pick_font(max(12,int(28*scale))),fill='#dddddd')
    label='UTC'+timestamp.astimezone(tz).strftime('%z')+' | Data: NICT / JMA'
    if held:label+=' | HELD FRAME: DATA GAP'
    draw.text((margin,height-max(18,int(48*scale))),label,font=pick_font(max(9,int(17*scale))),fill='#aaaaaa')
    return frame

def inspect_video(path,ffprobe='ffprobe'):
    return json.loads(subprocess.check_output([ffprobe,'-v','error','-count_frames','-select_streams','v:0',
        '-show_entries','stream=width,height,nb_read_frames,avg_frame_rate:format=duration','-of','json',str(path)],text=True))

def stream_segment(times,output,fps,loader=observation,ffmpeg='ffmpeg',ffprobe='ffprobe',width=1920,height=1080,tz=TZ,crf=18,preset='medium'):
    output=pathlib.Path(output);log=output.with_suffix('.log')
    heartbeat=output.parent/'progress.json'
    def mark(phase,number,timestamp):
        record={'phase':phase,'frame_in_day':number,'frames_in_day':len(times),'day':output.stem,
            'timestamp_utc':timestamp.isoformat(),'updated_unix':time.time()}
        pending=heartbeat.with_suffix('.tmp');pending.write_text(json.dumps(record),encoding='utf-8');pending.replace(heartbeat)
    command=[ffmpeg,'-hide_banner','-y','-f','rawvideo','-pix_fmt','rgb24','-s',f'{width}x{height}',
        '-r',str(fps),'-i','pipe:0','-an','-c:v','libx264','-preset',preset,'-crf',str(crf),
        '-pix_fmt','yuv420p','-movflags','+faststart',str(output)]
    with log.open('wb') as logfile:
        process=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=logfile)
        if os.name=='posix':os.set_blocking(process.stdin.fileno(),False)
        try:
            for number,timestamp in enumerate(times,1):
                mark('fetch_and_blend',number,timestamp)
                globe=loader(timestamp)
                frame=video_frame(timestamp,globe,width,height,tz)
                mark('encode',number,timestamp)
                data=frame.tobytes()
                if os.name=='posix':
                    view=memoryview(data);deadline=time.monotonic()+30
                    while len(view):
                        remaining=deadline-time.monotonic()
                        if remaining<=0:raise TimeoutError('FFmpeg input stalled for 30 seconds')
                        _,writable,_=select.select([], [process.stdin.fileno()], [], min(5,remaining))
                        if process.poll() is not None:raise RuntimeError('FFmpeg exited during encoding')
                        if writable:
                            try:view=view[os.write(process.stdin.fileno(),view):]
                            except BlockingIOError:pass
                else:process.stdin.write(data)
                frame.close();globe.close()
                mark('frame_complete',number,timestamp)
                if number%12==0 or number==len(times):print(f'HEARTBEAT {number}/{len(times)} | {timestamp.astimezone(tz):%Y-%m-%d %H:%M} | wall={time.time():.0f}',flush=True)
            process.stdin.close()
            if process.wait(timeout=120)!=0:raise RuntimeError('FFmpeg编码失败，查看：'+str(log))
        except BaseException:
            process.kill();process.wait()
            if process.stdin and not process.stdin.closed:process.stdin.close()
            raise
    report=inspect_video(output,ffprobe)
    stream=report['streams'][0]
    assert int(stream['nb_read_frames'])==len(times),'帧数不符'
    assert abs(float(report['format']['duration'])-len(times)/fps)<0.1,'时长不符'
    assert (stream['width'],stream['height'])==(width,height),'分辨率不符'
    return report
