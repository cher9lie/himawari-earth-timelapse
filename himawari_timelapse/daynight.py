"""NICT visible + simultaneous B13 cloud overlay, with solar night mask.

Navigation constants: https://himawari8.nict.go.jp/tileViewer/jquery-k2go-tile-viewer.js
Solar position: NOAA fractional-year approximation, UTC.
No static land texture, city lights, AI frames, or per-frame exposure changes.
"""
import datetime as dt
import io, math, concurrent.futures, functools, threading, time, pathlib,json,hashlib
import numpy as np
import requests
from PIL import Image, UnidentifiedImageError
from . import core

VISIBLE = core.BASE
INFRARED = 'https://himawari8-dl.nict.go.jp/himawari8/img/FULL_24h/B13'
POOL = concurrent.futures.ThreadPoolExecutor(max_workers=8)
HTTP=threading.local()
GAP_LOG=None
PLACEHOLDER_SHA256='4fe3ef5e19590aa04e260b83ab7a91eaf63d4e8dab1ff0d24f8cc7104126c260'
class MissingImage(RuntimeError):pass

def fetch_tile(url):
    # One session per worker preserves TLS connections without shared mutable cookies.
    if not hasattr(HTTP,'session'):
        HTTP.session=requests.Session()
        HTTP.session.headers['User-Agent']='EarthTimelapsePersonal/1.0'
    for attempt in range(4):
        try:
            with HTTP.session.get(url,timeout=(15,30)) as response:
                if response.status_code==404: raise MissingImage('Missing satellite image: '+url)
                response.raise_for_status()
                content=response.content
                # HTTP 200 can still contain an error page or a truncated PNG.
                # Validate before decoding so a transient response is retried.
                with Image.open(io.BytesIO(content)) as candidate:
                    candidate.verify()
                return content
        except requests.RequestException:
            if attempt==3: raise
            time.sleep(2**attempt)
        except (UnidentifiedImageError, OSError, SyntaxError) as error:
            if attempt==3:
                raise MissingImage('Undecodable satellite image after retries: '+url) from error
            time.sleep(2**attempt)

def tile(timestamp, base, x, y):
    url=f'{base}/2d/550/{timestamp:%Y/%m/%d/%H%M%S}_{x}_{y}.png'
    content=fetch_tile(url)
    with Image.open(io.BytesIO(content)) as source:
        source.load()
        if source.size != (550,550): raise RuntimeError('Unexpected tile size: '+url)
        if source.mode=='RGB' and hashlib.sha256(source.tobytes()).hexdigest()==PLACEHOLDER_SHA256:
            raise MissingImage('No Image placeholder: '+url)
        if base==INFRARED:
            if 'A' not in source.getbands() and 'transparency' not in source.info:
                raise MissingImage('B13 cloud opacity missing / No Image: '+url)
            return source.convert('RGBA').getchannel('A').copy()
        return source.convert('RGB')

@functools.lru_cache(maxsize=1)
def geographic_grid():
    size=1100
    axis=(np.arange(size,dtype=np.float64)+0.5)*11000/size
    x=np.deg2rad((axis-5500.5)*65536/40932549)[None,:]
    y=np.deg2rad((axis-5500.5)*65536/40932549)[:,None]
    coefficient=np.cos(y)**2+1.006739501*np.sin(y)**2
    radial=42164*np.cos(x)*np.cos(y)
    discriminant=radial**2-coefficient*1737122264
    valid=discriminant>=0
    distance=(radial-np.sqrt(np.maximum(discriminant,0)))/coefficient
    vx=42164-distance*np.cos(x)*np.cos(y)
    vy=distance*np.sin(x)*np.cos(y)
    vz=-distance*np.sin(y)
    lon=np.arctan2(vy,vx)+np.deg2rad(140.7)
    lat=np.arctan2(1.006739501*vz,np.hypot(vx,vy))
    return lat.astype(np.float32),lon.astype(np.float32),valid

def night_weight(timestamp):
    utc=timestamp.astimezone(dt.timezone.utc)
    minutes=utc.hour*60+utc.minute+utc.second/60
    gamma=2*math.pi/365*(utc.timetuple().tm_yday-1+(minutes/60-12)/24)
    equation=229.18*(0.000075+0.001868*math.cos(gamma)-0.032077*math.sin(gamma)
        -0.014615*math.cos(2*gamma)-0.040849*math.sin(2*gamma))
    declination=(0.006918-0.399912*math.cos(gamma)+0.070257*math.sin(gamma)
        -0.006758*math.cos(2*gamma)+0.000907*math.sin(2*gamma)
        -0.002697*math.cos(3*gamma)+0.00148*math.sin(3*gamma))
    lat,lon,valid=geographic_grid()
    hour_angle=np.deg2rad((minutes+equation)/4-180)+lon
    cosine=np.sin(lat)*math.sin(declination)+np.cos(lat)*math.cos(declination)*np.cos(hour_angle)
    # Day-side pixels are unchanged; transition occurs from sunset to -8 degrees.
    transition=np.clip(-cosine/math.sin(math.radians(8)),0,1)
    return (transition*transition*(3-2*transition)*valid).astype(np.float32)

def blend(timestamp,visible,infrared_alpha):
    colors=np.asarray(visible,dtype=np.float32)
    alpha=np.asarray(infrared_alpha,dtype=np.float32)/255
    if np.count_nonzero(alpha)>0:
        background=np.array([4,8,15],dtype=np.float32)
        cloud=np.array([220,225,232],dtype=np.float32)
        night=background[None,None,:]+alpha[:,:,None]*(cloud-background)[None,None,:]
        night*=geographic_grid()[2][:,:,None]
        weight=night_weight(timestamp)[:,:,None]
        result=np.clip(colors*(1-weight)+night*weight,0,255).astype(np.uint8)
        return Image.fromarray(result)
    raise RuntimeError('Empty infrared cloud overlay at '+timestamp.isoformat())

def exact_observation(timestamp):
    visible=Image.new('RGB',(1100,1100))
    opacity=Image.new('L',(1100,1100))
    jobs=[(base,x,y,POOL.submit(tile,timestamp,base,x,y))
        for base in [VISIBLE,INFRARED] for y in range(2) for x in range(2)]
    try:
        for base,x,y,future in jobs:
            image=future.result(timeout=45)
            (opacity if base==INFRARED else visible).paste(image,(550*x,550*y))
            image.close()
        return blend(timestamp,visible,opacity)
    finally:
        visible.close();opacity.close()

def observation(timestamp):
    try:return exact_observation(timestamp)
    except MissingImage as missing:
        for delta in [-10,10,-20,20]:
            actual=timestamp+dt.timedelta(minutes=delta)
            try:image=exact_observation(actual)
            except MissingImage:continue
            image.info['held_frame']=True
            image.info['capture_time']=actual.isoformat()
            record={'nominal_utc':timestamp.isoformat(),'actual_observation_utc':actual.isoformat(),
                'offset_minutes':delta,'reason':str(missing),'policy':'Whole synchronized visible/IR pair; no spatial or temporal interpolation'}
            if GAP_LOG:
                with pathlib.Path(GAP_LOG).open('a',encoding='utf-8') as output:output.write(json.dumps(record)+'\n')
            print('DATA GAP',timestamp.isoformat(),'using',actual.isoformat(),flush=True)
            return image
        raise MissingImage('No complete visible/IR pair within 20 minutes of '+timestamp.isoformat()) from missing
