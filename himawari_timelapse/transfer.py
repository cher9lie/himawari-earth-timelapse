"""Bounded final-video transfers; verify each chunk and the complete file."""
import hashlib
import json
from pathlib import Path
import shutil
from .job import sha256, atomic_json

CHUNK_SIZE=8*2**20

def download_verified(manifest, target, fetch_chunk):
    """fetch_chunk(offset, size) returns (local chunk path, source SHA256)."""
    target=Path(target);target.parent.mkdir(parents=True,exist_ok=True)
    partial=target.with_suffix('.mp4.part')
    if target.exists():
        if target.stat().st_size==manifest['bytes'] and sha256(target)==manifest['sha256']:return target
        raise FileExistsError('目标文件已经存在但与源不同，请更换成片名称')
    if partial.exists() and partial.stat().st_size>manifest['bytes']:
        raise ValueError('续传文件比云端文件大，保留原文件，请更换下载位置')
    size=partial.stat().st_size if partial.exists() else 0
    # Allow one incoming chunk plus headroom, including the eventual append.
    required=manifest['bytes']-size+2*CHUNK_SIZE+64*2**20
    if shutil.disk_usage(target.parent).free<required:
        raise OSError(f'下载盘空间不足，至少还需 {required/2**20:.1f} MiB')
    if size%CHUNK_SIZE and size!=manifest['bytes']:
        # Previous interrupted append is discarded only to the last verified boundary.
        with partial.open('r+b') as file:file.truncate(size-size%CHUNK_SIZE)
    for offset in range(0,manifest['bytes'],CHUNK_SIZE):
        length=min(CHUNK_SIZE,manifest['bytes']-offset)
        chunk, expected=fetch_chunk(offset,length)
        chunk=Path(chunk)
        if chunk.stat().st_size!=length or sha256(chunk)!=expected:
            raise ValueError('视频分块校验失败；已有文件保留')
        existing=partial.stat().st_size if partial.exists() else 0
        if existing>=offset+length:
            with partial.open('rb') as source:
                source.seek(offset)
                if hashlib.sha256(source.read(length)).hexdigest()!=expected:
                    raise ValueError('已下载前缀校验失败；保留文件，请使用新下载位置')
        else:
            if existing!=offset:raise ValueError('非连续续传位置')
            try:
                with partial.open('ab') as output,chunk.open('rb') as source:shutil.copyfileobj(source,output)
            except BaseException:
                with partial.open('r+b') as output:output.truncate(offset)
                raise
        chunk.unlink()
        print(f'DOWNLOAD {offset+length}/{manifest["bytes"]} bytes',flush=True)
    if sha256(partial)!=manifest['sha256']:raise ValueError('完整 SHA256 不符')
    partial.replace(target)
    atomic_json(target.with_suffix('.validation.json'),manifest)
    return target
