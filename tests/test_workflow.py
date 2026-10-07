from dataclasses import replace
from datetime import datetime, timezone, timedelta
import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import numpy as np
from PIL import Image
from himawari_timelapse.config import Config, estimate
from himawari_timelapse import daynight, core
from himawari_timelapse.job import run, sha256, validate
from himawari_timelapse.transfer import download_verified, CHUNK_SIZE

class ConfigurationTests(unittest.TestCase):
    def test_july_and_estimates(self):
        c=Config().validate();self.assertEqual(c.frames,4464)
        self.assertEqual(estimate(c)['duration_seconds'],297.6)
        self.assertIsNone(estimate(c)['time_seconds'])
        measured={'config_key':c.key,'seconds_per_frame':2,'frames':12}
        self.assertEqual(estimate(c,measured)['time_seconds'],[7142,16070])
        with self.assertRaises(ValueError):estimate(replace(c,width=1280),measured)

    def test_invalid_and_continuous_cadence(self):
        for c in [Config(interval_minutes=5),Config(end='2026-07-01'),Config(width=1919),Config(fps=0),Config(output_name='../x.mp4')]:
            with self.assertRaises(ValueError):c.validate()
        times=list(Config(end='2026-07-03',interval_minutes=50).times())
        self.assertEqual(len(times),58)
        self.assertTrue(all(b-a==timedelta(minutes=50) for a,b in zip(times,times[1:])))
        self.assertEqual(Config().key,replace(Config(),download_dir='other',session='new').key)

class ImageTests(unittest.TestCase):
    def test_daylight_unchanged(self):
        timestamp=datetime(2026,7,15,3,tzinfo=timezone.utc)
        colors=np.random.default_rng(42).integers(0,256,(1100,1100,3),dtype=np.uint8)
        result=np.asarray(daynight.blend(timestamp,Image.fromarray(colors),Image.new('L',(1100,1100),128)))
        weight=daynight.night_weight(timestamp)
        np.testing.assert_array_equal(result[weight==0],colors[weight==0])
        self.assertFalse(np.array_equal(result[weight==1],colors[weight==1]))

    def test_whole_pair_gap_and_actual_timestamp(self):
        timestamp=datetime(2026,7,1,tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            log=Path(directory)/'gaps.jsonl'
            def exact(value):
                if value==timestamp:raise daynight.MissingImage('gap')
                return Image.new('RGB',(1100,1100),(10,20,30))
            with patch.object(daynight,'exact_observation',side_effect=exact),patch.object(daynight,'GAP_LOG',log):
                image=daynight.observation(timestamp)
                self.assertTrue(image.info['held_frame'])
                actual=(timestamp-timedelta(minutes=10)).isoformat()
                self.assertEqual(image.info['capture_time'],actual)
                self.assertEqual(json.loads(log.read_text())['actual_observation_utc'],actual)
                with patch.object(core.ImageDraw,'Draw') as drawing:
                    core.video_frame(timestamp,image)
                    self.assertIn('2026-07-01 07:50',str(drawing.return_value.text.call_args_list))
            with patch.object(daynight,'exact_observation',side_effect=daynight.MissingImage('long gap')):
                with self.assertRaises(daynight.MissingImage):daynight.observation(timestamp)

    def test_bad_png_retried(self):
        buffer=io.BytesIO();Image.new('RGB',(2,2)).save(buffer,format='PNG')
        class Response:
            status_code=200
            def __init__(self,data):self.content=data
            def __enter__(self):return self
            def __exit__(self,*args):return False
            def raise_for_status(self):pass
        fake=type('Session',(),{'get':lambda *a,**k:None})()
        with patch.object(daynight.HTTP,'session',fake,create=True),patch.object(fake,'get',side_effect=[Response(b'bad'),Response(buffer.getvalue())]) as get,patch.object(daynight.time,'sleep'):
            self.assertEqual(daynight.fetch_tile('https://example.invalid/tile'),buffer.getvalue())
            self.assertEqual(get.call_count,2)

class TransferTests(unittest.TestCase):
    def test_resume_checks_existing_chunks(self):
        with tempfile.TemporaryDirectory() as directory:
            target=Path(directory)/'video.mp4';chunk=Path(directory)/'chunk.bin'
            data=b'a'*CHUNK_SIZE+b'b'*19
            manifest={'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()}
            target.with_suffix('.mp4.part').write_bytes(data[:CHUNK_SIZE]+b'partial')
            def fetch(offset,length):
                value=data[offset:offset+length];chunk.write_bytes(value)
                return chunk,hashlib.sha256(value).hexdigest()
            result=download_verified(manifest,target,fetch)
            self.assertEqual(result.read_bytes(),data)
            self.assertEqual(download_verified(manifest,target,fetch),target)

    def test_corruption_and_disk_space(self):
        with tempfile.TemporaryDirectory() as directory:
            target=Path(directory)/'video.mp4';chunk=Path(directory)/'chunk.bin'
            data=b'a'*CHUNK_SIZE
            manifest={'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()}
            target.with_suffix('.mp4.part').write_bytes(b'b'*CHUNK_SIZE)
            def fetch(offset,length):
                chunk.write_bytes(data);return chunk,manifest['sha256']
            with self.assertRaisesRegex(ValueError,'前缀'):download_verified(manifest,target,fetch)
            with patch('himawari_timelapse.transfer.shutil.disk_usage',return_value=shutil._ntuple_diskusage(1,1,0)):
                with self.assertRaises(OSError):download_verified(manifest,target,fetch)

@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'),'FFmpeg not installed')
class EncodingTests(unittest.TestCase):
    def test_multiday_encode_resume_and_corruption_repair(self):
        with tempfile.TemporaryDirectory() as directory:
            c=Config(start='2026-07-01',end='2026-07-03',interval_minutes=720,fps=2,width=640,height=360,
                     save_root=directory+'/persistent',scratch=directory+'/scratch',preset='ultrafast')
            def loader(timestamp):return Image.new('RGB',(1100,1100),(70,110,160))
            final=run(c,loader);report=validate(final,c,4)
            self.assertEqual(report['format']['duration'],'2.000000')
            def unexpected(timestamp):raise AssertionError('Completed day regenerated')
            self.assertEqual(run(c,unexpected),final)
            day=final.parent/'20260701.mp4';day.write_bytes(b'corrupt')
            run(c,loader)
            self.assertEqual(json.loads((final.parent/'20260701.json').read_text())['sha256'],sha256(day))
            self.assertFalse(list(Path(directory).rglob('*.png')))

class CloudOrchestrationTests(unittest.TestCase):
    def test_no_second_exec_for_active_production(self):
        from himawari_timelapse import cli
        with tempfile.TemporaryDirectory() as directory:
            config=Config(workspace=directory)
            calls=[]
            def fake(config,*args,**kwargs):
                calls.append(args)
                if args[0]=='download':
                    Path(args[-1]).write_text(json.dumps({'phase':'rendering'}))
                return subprocess.CompletedProcess(args,0,stdout='existing session',stderr='')
            with patch.object(cli,'colab',side_effect=fake):
                with self.assertRaisesRegex(RuntimeError,'第二条 exec'):cli.cloud(config,'run')
            self.assertEqual([c[0] for c in calls],['status','ls','download'])

    def test_complete_job_retrieves_without_rerender(self):
        from himawari_timelapse import cli
        with tempfile.TemporaryDirectory() as directory:
            config=Config(workspace=directory)
            def fake(config,*args,**kwargs):
                if args[0]=='download':Path(args[-1]).write_text(json.dumps({'phase':'complete'}))
                return subprocess.CompletedProcess(args,0,stdout='existing session',stderr='')
            with patch.object(cli,'colab',side_effect=fake),patch.object(cli,'collect') as collect:
                cli.cloud(config,'run');collect.assert_called_once()

if __name__=='__main__':unittest.main()
