"""Validated job settings and honest capacity estimates; no network required."""
from dataclasses import dataclass, asdict
from datetime import datetime, timezone, timedelta
import hashlib
import json
import math
from pathlib import Path

@dataclass
class Config:
    start: str = "2026-07-01"
    end: str = "2026-08-01"
    interval_minutes: int = 10
    fps: int = 15
    width: int = 1920
    height: int = 1080
    timezone_offset: float = 8
    crf: int = 18
    preset: str = "medium"
    workers: int = 8
    workspace: str = "./workspace"
    download_dir: str = "./downloads"
    save_root: str = "/content/drive/MyDrive/EarthTimelapse"
    scratch: str = "/content/earth_video_work"
    session: str = "earth-timelapse"
    output_name: str = "earth_timelapse.mp4"

    def validate(self):
        # Calendar dates only: each date begins at midnight in the selected offset.
        for value in [self.start, self.end]:
            if datetime.strptime(value, "%Y-%m-%d").strftime("%Y-%m-%d") != value:
                raise ValueError("日期须为 YYYY-MM-DD")
        if self.end <= self.start:
            raise ValueError("结束日期必须晚于开始日期（不包含结束日期）")
        for value in [self.interval_minutes, self.fps, self.width, self.height, self.crf, self.workers]:
            if type(value) is not int:
                raise ValueError("间隔、帧率、尺寸、CRF、并发数必须为整数")
        if not 10 <= self.interval_minutes <= 1440 or self.interval_minutes % 10:
            raise ValueError("采样间隔须为 10 的整数倍，范围 10–1440 分钟")
        if not 1 <= self.fps <= 60 or not 1 <= self.workers <= 8:
            raise ValueError("FPS 范围 1–60；并发数范围 1–8")
        if any(v % 2 for v in [self.width, self.height]) or not (320 <= self.width <= 7680 and 240 <= self.height <= 4320):
            raise ValueError("尺寸须为偶数；宽 320–7680，高 240–4320")
        if not 0 <= self.crf <= 51 or self.preset not in ["ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow"]:
            raise ValueError("无效编码参数")
        if not math.isfinite(self.timezone_offset) or not -12 <= self.timezone_offset <= 14 or self.timezone_offset * 60 % 1:
            raise ValueError("UTC 时差须为整分钟，范围 -12 到 +14")
        if not self.session or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for c in self.session):
            raise ValueError("会话名只允许字母、数字、短横线和下划线")
        if not self.output_name.endswith(".mp4") or Path(self.output_name).name != self.output_name or any(c in self.output_name for c in '\\/:'):
            raise ValueError("成片名称须为不含路径的 .mp4 文件名")
        for value in [self.workspace, self.download_dir, self.save_root, self.scratch]:
            if not value or any(c in value for c in "\n\r\x00"):
                raise ValueError("工作路径不能为空，也不能含换行或 NUL")
        return self

    @property
    def tz(self):
        return timezone(timedelta(hours=self.timezone_offset))

    def times(self):
        current = datetime.fromisoformat(self.start).replace(tzinfo=self.tz)
        end = datetime.fromisoformat(self.end).replace(tzinfo=self.tz)
        while current < end:
            yield current.astimezone(timezone.utc)
            current += timedelta(minutes=self.interval_minutes)

    @property
    def frames(self):
        return math.ceil((datetime.fromisoformat(self.end)-datetime.fromisoformat(self.start)).total_seconds() / (60*self.interval_minutes))

    @property
    def key(self):
        fields = ["start", "end", "interval_minutes", "fps", "width", "height", "timezone_offset", "crf", "preset", "workers"]
        payload = {k: getattr(self, k) for k in fields}
        payload.update(source="NICT D531106 + synchronized FULL_24h/B13 2d550", revision=2, gap_minutes=20)
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:12]

    def save(self, path):
        Path(path).write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path):
        return cls(**json.loads(Path(path).read_text(encoding="utf-8-sig"))).validate()

def estimate(config, calibration=None):
    config.validate()
    duration = config.frames/config.fps
    # Capacity planning assumption, NOT a fixed bitrate guarantee for CRF encoding.
    scale = config.width*config.height/(1920*1080)
    bitrate = [6*scale, 14*scale]
    sizes = [duration*v*1_000_000/8 for v in bitrate]
    result = dict(frames=config.frames, duration_seconds=duration, tile_requests_minimum=config.frames*8,
                  assumed_bitrate_mbps=bitrate, final_mib=[round(v/2**20, 1) for v in sizes],
                  drive_peak_mib=round(2*sizes[1]/2**20, 1), scratch_peak_mib=round(3*sizes[1]/2**20+64, 1),
                  local_download_peak_mib=round(sizes[1]/2**20+96, 1), raw_images_saved=0,
                  time_seconds=None, time_basis="未测量；请运行 benchmark。空间按1080p 6–14 Mbps假设估算，CRF/内容会改变实际大小。")
    if calibration:
        if calibration["config_key"] != config.key:
            raise ValueError("基准参数不匹配，请重新测量")
        seconds = calibration["seconds_per_frame"]
        if not math.isfinite(seconds) or seconds <= 0:
            raise ValueError("无效基准速度")
        result.update(time_seconds=[round(config.frames*seconds*.8), round(config.frames*seconds*1.8)],
                      time_basis=f"实测 {calibration['frames']} 帧；含下载、融合和编码。范围为实测外推×0.8–1.8；不含授权、排队、长缺测和成片取回。")
    return result
