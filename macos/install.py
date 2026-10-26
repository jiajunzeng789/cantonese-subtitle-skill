"""Offline, per-user Apple Silicon installer; no sudo, Homebrew, or online pip."""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import time


def atomic(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp=path.with_name(path.name+'.new')
    temp.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
    os.replace(temp,path)


def digest(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        while block:=f.read(8*1024*1024):h.update(block)
    return h.hexdigest()


def run(command, env, log, timeout=1200):
    print('🔄 '+str(command[1] if len(command)>1 else command[0]),flush=True)
    with log.open('ab') as f:
        p=subprocess.Popen([str(x) for x in command],env=env,stdout=f,stderr=subprocess.STDOUT)
        started=time.monotonic();last=started
        try:
            while p.poll() is None:
                if time.monotonic()-started>timeout:raise TimeoutError(f'超过{timeout}秒')
                if time.monotonic()-last>=20:
                    tail=log.read_bytes()[-1800:].decode('utf-8',errors='replace').splitlines()
                    print(f'⏳ 当前步骤已运行{int(time.monotonic()-started)}秒  '+(tail[-1] if tail else '等待输出'),flush=True)
                    last=time.monotonic()
                time.sleep(.2)
            if p.returncode:
                print(log.read_bytes()[-4000:].decode('utf-8',errors='replace'),flush=True)
                raise RuntimeError(f'步骤退出码{p.returncode}，日志：{log}')
        finally:
            if p.poll() is None:p.terminate();p.wait(timeout=15)


def copy_tree(source, target):
    if not target.exists():
        # APFS clone avoids duplicating model storage when supported. It is not required.
        r=subprocess.run(['/bin/cp','-cRp',str(source),str(target)],capture_output=True)
        if r.returncode==0:return
    shutil.copytree(source,target,symlinks=True,dirs_exist_ok=True)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--payload',type=Path,required=True)
    p.add_argument('--user-home',type=Path,default=Path.home(),help='Explicit test/user destination; never changes HOME')
    p.add_argument('--install-root',type=Path)
    a=p.parse_args();payload=a.payload.resolve();user=a.user_home.absolute()
    if platform.system()!='Darwin' or platform.machine()!='arm64':raise RuntimeError('需要苹果芯片Mac原生运行')
    if int(platform.mac_ver()[0].split('.')[0])<14:raise RuntimeError('此版本需要macOS 14或更新系统')
    manifest=json.loads((payload/'bundle.json').read_text())
    root=(a.install_root or user/'Library/Application Support/CantoneseSubtitleSkill').absolute()
    root.mkdir(parents=True,exist_ok=True)
    lock=root/'install.lock'
    try:
        with lock.open('x') as f:json.dump(dict(pid=os.getpid(),started=time.time()),f)
    except FileExistsError:
        prior=json.loads(lock.read_text());pid=int(prior['pid'])
        try:os.kill(pid,0)
        except ProcessLookupError:lock.unlink()
        else:raise RuntimeError(f'另一安装进程仍在运行（PID {pid}），请勿重复启动')
        with lock.open('x') as f:json.dump(dict(pid=os.getpid(),started=time.time()),f)
    build=digest(payload/'bundle.json')[:12]
    version=root/'versions'/f"{manifest['version']}-{build}"
    state=root/'install-state.json';logs=root/'logs';logs.mkdir(exist_ok=True)
    log=logs/(datetime.datetime.now().strftime('%Y%m%d-%H%M%S')+'.log')
    def status(stage,state_value='running',**values):
        atomic(state,dict(stage=stage,status=state_value,version=str(version),log=str(log),**values))
        print(('✅ ' if state_value=='passed' else '🔄 ')+stage,flush=True)
    try:
        status('检查安装包完整性')
        required=sum(f['size'] for f in manifest['files'] if f['kind']=='file')
        if shutil.disk_usage(root).free<required+5*1024**3:raise RuntimeError('可用磁盘不足，请至少预留安装包解压大小加5GB空间')
        last=time.monotonic()
        for i,row in enumerate(manifest['files']):
            rel=Path(row['path'])
            if rel.is_absolute() or '..' in rel.parts:raise ValueError('安装包含非法路径')
            item=payload/rel
            if row['kind']=='symlink':
                if not item.is_symlink() or os.readlink(item)!=row['target']:raise RuntimeError(f'链接损坏：{rel}')
                if not item.resolve().is_relative_to(payload):raise RuntimeError('安装包链接越界')
            elif not item.is_file() or item.stat().st_size!=row['size'] or digest(item)!=row['sha256']:
                raise RuntimeError(f'文件损坏或缺失：{rel}，请重新获取完整安装包')
            if time.monotonic()-last>15:
                print(f'⏳ 已核对{i+1}/{len(manifest["files"])}个文件',flush=True);last=time.monotonic()
        status('安装包校验通过','passed')
        version.mkdir(parents=True,exist_ok=True)
        status('准备独立Python 媒体工具与本地模型')
        for folder in ['python','tools','models','skill','fixtures','wheelhouse']:
            copy_tree(payload/folder,version/folder)
        shutil.copy2(payload/'requirements.lock',version/'requirements.lock')
        env=dict(os.environ,PATH=str(version/'tools/bin')+os.pathsep+os.defpath,
                 CANTONESE_SUBTITLES_TOOLS=str(version/'tools/bin'),HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',
                 PIP_NO_INDEX='1',PIP_CONFIG_FILE=os.devnull,PYTHONUNBUFFERED='1',PYTHONNOUSERSITE='1')
        for k in ['PYTHONPATH','PYTHONHOME','PIP_INDEX_URL','PIP_EXTRA_INDEX_URL','PIP_TRUSTED_HOST']:env.pop(k,None)
        base=version/'python/bin/python3';python=version/'runtime/bin/python'
        if not python.exists():run([base,'-m','venv',version/'runtime'],env,log,120)
        status('离线安装全部Python依赖')
        run([python,'-m','pip','install','--no-index','--find-links',version/'wheelhouse',
             '--only-binary=:all:','-r',version/'requirements.lock'],env,log)
        (version/'runtime/.cantonese-subtitles-managed').write_text('Created by macOS offline installer\n')
        run([python,'-m','pip','check'],env,log,120)
        scripts=version/'skill/scripts';workspace=version/'verification'
        manifest_path=version/'models.local.json'
        models={name:dict(path=str(version/'models'/name),repo=repo,load_verified=False) for name,repo in manifest['models'].items()}
        atomic(manifest_path,models)
        status('检查媒体解码和运行环境')
        report=version/'environment.local.json'
        run([python,scripts/'check_runtime.py','--workspace',workspace,'--report',report],env,log,240)
        status('依次加载模型并识别内置粤语测试音频')
        checked=json.loads(report.read_text());device='mps' if checked.get('devices',{}).get('mps') else 'cpu'
        run([python,scripts/'verify_models.py','--workspace',workspace,'--manifest',manifest_path,
             '--sample',version/'fixtures/installation-test.wav','--sample-seconds','8','--qwen-device',device],env,log,1200)
        evidence=json.loads((workspace/'.cantonese-subtitles/models-check.local.json').read_text())
        if not evidence['sample_inference_verified']:raise RuntimeError('模型短音频识别未通过，未宣布安装成功')
        status('检查Whisper时间校准与SRT导出')
        sample=version/'fixtures/installation-test.wav'
        sample_duration=float(subprocess.check_output([str(version/'tools/bin/ffprobe'),'-v','error',
            '-show_entries','format=duration','-of','default=nw=1:nk=1',str(sample)],text=True))
        test=workspace/f'subtitle-test-{time.time_ns()}'
        edited=test.with_suffix('.input.json');aligned=test.with_suffix('.aligned.json')
        atomic(edited,dict(media_duration=sample_duration,subtitle_profile=dict(layout='landscape'),cues=[dict(
            start=0,end=sample_duration,original='呢架車開起身好舒服 我哋而家開始製作字幕',
            text='这辆车很舒适现在开始制作字幕')]))
        run([python,scripts/'realign_subtitles.py','--media',sample,'--input',edited,
             '--model',version/'models/whisper','--output',aligned],env,log,240)
        run([python,scripts/'export_srt.py',aligned,test.with_suffix('.srt')],env,log,120)
        stamp=datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
        status('安装完整Skill到Agent目录')
        for agent,dest in [('Codex',user/'.codex/skills/auto-cantonese-subtitles'),
                           ('WorkBuddy',user/'.workbuddy/skills/auto-cantonese-subtitles'),
                           ('通用Agent',user/'.agents/skills/auto-cantonese-subtitles')]:
            if dest.is_symlink():raise RuntimeError(f'Skill目录是链接，请先确认目标：{dest}')
            for source in (version/'skill').rglob('*'):
                if not source.is_file():continue
                rel=source.relative_to(version/'skill');target=dest/rel
                if target.exists() and target.read_bytes()!=source.read_bytes():
                    backup=root/'backups'/stamp/agent/rel;backup.parent.mkdir(parents=True,exist_ok=True)
                    shutil.copy2(target,backup)
                target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,target)
        record={**checked,'tools_bin':str(version/'tools/bin'),'models':models,'models_manifest':str(manifest_path),
                'model_load_verified':True,'sample_inference_verified':True,'alignment_self_test_verified':True,
                'installer_version':manifest['version']}
        atomic(user/'.config/cantonese-subtitles/environment.local.json',record)
        current=root/'current';temp=root/'current.new'
        if temp.is_symlink():temp.unlink()
        temp.symlink_to(version,target_is_directory=True);os.replace(temp,current)
        work=user/'粤语字幕工作台';work.mkdir(exist_ok=True)
        (work/'开始使用.txt').write_text('安装已完成。在WorkBuddy或其他Agent开启新对话，粘贴：\n\n'
            '请使用“粤语字幕制作skill”处理我提供的素材，输出横屏单行简体书面语SRT，无句读标点，连续讲话时保留到下一条出现。'
            '本机已安装完整离线环境，请读取 ~/.config/cantonese-subtitles/environment.local.json 并复用，不要重新下载或安装。\n\n'
            '将音视频文件交给Agent即可。语音识别在本地，文字校准使用当前Agent的语言模型。\n',encoding='utf-8')
        status('安装和真实语音自检全部通过','passed',workspace=str(work))
        print(f'\n✅ 已安装到：{root}\n✅ 工作文件夹：{work}\n现在打开WorkBuddy新对话，提供音视频即可。',flush=True)
    except BaseException as e:
        status('安装中断','failed',error=str(e));raise
    finally:
        lock.unlink(missing_ok=True)


if __name__=='__main__':
    try:main()
    except KeyboardInterrupt:print('⏸️ 已中断，完成部分和日志已保留',flush=True);sys.exit(130)
    except Exception as e:print(f'❌ {e}',flush=True);sys.exit(1)
