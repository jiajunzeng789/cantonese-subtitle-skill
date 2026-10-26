"""Assemble an offline Apple Silicon ZIP from verified local tools, wheels and public models."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import sys
import time
import zipfile

HERE=Path(__file__).resolve().parent


def digest(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        while block:=f.read(8*1024*1024):h.update(block)
    return h.hexdigest()


def copy(source,target,symlinks=False):
    print('🔄 打包 '+target.name,flush=True)
    if target.exists():raise RuntimeError(f'目标已存在：{target}')
    excluded=['.cache','.git','__pycache__','*.pyc','.DS_Store']
    if target.name=='skill':
        excluded+=['.venv','venv','.cantonese-subtitles','.env','.env.*','outputs','runs','test-media',
                   '*.mp3','*.MP3','*.mp4','*.MP4','*.wav','*.srt','*.local.json']
    shutil.copytree(source,target,symlinks=symlinks,ignore=shutil.ignore_patterns(*excluded))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    if sys.version_info<(3,13):p.error('打包脚本需要Python 3.13及以上；可直接使用待打包的独立Python')
    for name in ['runtime','tools','wheels','lockfile','whisper','qwen','test-audio','ffmpeg-source','licenses','output']:
        p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--version',required=True)
    a=p.parse_args();a.output.mkdir(parents=True,exist_ok=True)
    package=a.output/('粤语字幕制作_苹果芯片离线安装包_'+a.version)
    package.mkdir();payload=package/'payload';payload.mkdir()
    copy(a.runtime,payload/'python',symlinks=True)
    copy(a.tools,payload/'tools')
    copy(a.wheels,payload/'wheelhouse')
    copy(HERE.parent/'skills/auto-cantonese-subtitles',payload/'skill')
    (payload/'models').mkdir()
    copy(a.whisper,payload/'models/whisper')
    copy(a.qwen,payload/'models/qwen')
    (payload/'fixtures').mkdir()
    shutil.copy2(a.test_audio,payload/'fixtures/installation-test.wav')
    shutil.copy2(a.lockfile,payload/'requirements.lock')
    (payload/'third-party').mkdir()
    copy(a.licenses,payload/'third-party/licenses')
    shutil.copy2(a.ffmpeg_source,payload/'third-party'/a.ffmpeg_source.name)
    (payload/'third-party/说明.txt').write_text(
        'Python：Astral python-build-standalone，发行版自带许可证与组件说明。\n'
        'FFmpeg：8.1.3，LGPL 2.1或以后版本。对应源代码随包提供，未启用GPL或nonfree组件。\n'
        '构建选项：--disable-autodetect --disable-shared --enable-static --disable-doc --disable-debug '
        '--disable-ffplay --disable-network --enable-videotoolbox；最低macOS部署版本14.0。\n'
        'Whisper large-v3：OpenAI Whisper / Systran CTranslate2转换，MIT。\n'
        'Qwen3-ASR-1.7B：Qwen，Apache-2.0。模型目录中的上游说明与许可一并保留。\n'
        '各Python依赖的许可证包含在wheel中的dist-info及安装后的环境中。\n'
        '自检音频为本机macOS Sinji系统语音合成，不包含用户录音。\n',encoding='utf-8')
    for name in ['一键安装.command','install.py','先看这里.txt']:
        shutil.copy2(HERE/name,package/name)
    (package/'一键安装.command').chmod(0o755)
    manifest=dict(version=a.version,architecture='arm64',minimum_macos='14.0',
        models=dict(whisper='Systran/faster-whisper-large-v3',qwen='Qwen/Qwen3-ASR-1.7B'),files=[])
    for item in sorted(payload.rglob('*')):
        if item.is_symlink():
            if not item.resolve().is_relative_to(payload):raise RuntimeError(f'包内链接越界：{item}')
            row=dict(path=str(item.relative_to(payload)),kind='symlink',target=os.readlink(item))
        elif item.is_file():row=dict(path=str(item.relative_to(payload)),kind='file',size=item.stat().st_size,sha256=digest(item))
        else:continue
        manifest['files'].append(row)
    (payload/'bundle.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    archive=package.with_name(package.name+'.zip')
    if archive.exists():raise RuntimeError('ZIP已存在，拒绝覆盖')
    size=sum(r.get('size',0) for r in manifest['files']);done=0;last=time.monotonic()
    with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=1,allowZip64=True) as z:
        for item in sorted(package.rglob('*')):
            rel=str(item.relative_to(package.parent))
            if item.is_symlink():
                info=zipfile.ZipInfo(rel);info.create_system=3;info.external_attr=(stat.S_IFLNK|0o777)<<16
                z.writestr(info,os.readlink(item));continue
            if not item.is_file():continue
            info=zipfile.ZipInfo.from_file(item,rel);info.compress_type=zipfile.ZIP_DEFLATED
            info.compress_level=1
            with item.open('rb') as src,z.open(info,'w',force_zip64=True) as dst:
                while block:=src.read(4*1024*1024):
                    dst.write(block);done+=len(block)
                    if time.monotonic()-last>15:
                        print(f'⏳ 压缩进度 {done/1024**3:.2f}/{size/1024**3:.2f} GiB',flush=True);last=time.monotonic()
    archive.with_suffix('.zip.sha256').write_text(digest(archive)+'  '+archive.name+'\n')
    print('✅ '+str(archive),flush=True)


if __name__=='__main__':main()
