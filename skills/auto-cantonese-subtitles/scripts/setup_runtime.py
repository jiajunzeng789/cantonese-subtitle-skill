"""One local setup entrypoint: reuse verified environments, otherwise install in an isolated venv."""
import argparse
import json
import os
from pathlib import Path
import platform
import shutil
import signal
import subprocess
import sys
from urllib.parse import urlsplit
from runtime_common import Progress, SetupLock, atomic_json, index_url, now, run_logged

HERE = Path(__file__).resolve().parent
TUNA = 'https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple'
PYPI = 'https://pypi.org/simple'


def interpreter(venv):
    return Path(venv)/('Scripts/python.exe' if os.name == 'nt' else 'bin/python')


def candidates(a, state):
    found = []
    if a.python:
        found.append(a.python)
    for file in [state/'environment.local.json',
                 Path.home()/'.config/cantonese-subtitles/environment.local.json']:
        if file.exists():
            try:
                found.append(Path(json.loads(file.read_text())['python_executable']))
            except (ValueError, KeyError):
                pass
    found.extend([interpreter(a.venv), interpreter(a.workspace/'.venv'),
                  interpreter(HERE.parent/'.venv'), Path(sys.executable)])
    # Do not resolve venv symlinks: that would execute the base interpreter.
    return list(dict.fromkeys(p.absolute() for p in found if p and p.is_file()))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--workspace', type=Path, required=True)
    p.add_argument('--python', type=Path, help='Existing runtime candidate; never modified by this script')
    p.add_argument('--base-python', type=Path, default=Path(sys.executable))
    p.add_argument('--venv', type=Path, help='New managed environment location')
    p.add_argument('--fresh', action='store_true', help='Ignore candidates and require a new empty venv path')
    p.add_argument('--offline', action='store_true', help='Reuse only; do not install or download')
    p.add_argument('--index-url', default='auto', help='auto checks TUNA, then PyPI; or explicit HTTPS URL')
    p.add_argument('--share', action='store_true', help='Save successful runtime path in personal config')
    p.add_argument('--manifest', type=Path, help='Existing local model manifest, for optional model/sample checks')
    p.add_argument('--sample', type=Path, help='Original short media; requires --manifest')
    p.add_argument('--qwen-device', choices=['auto', 'cpu', 'mps', 'cuda'], default='auto')
    p.add_argument('--timeout', type=int, default=1200, help='Maximum seconds per subprocess step')
    a = p.parse_args()
    if a.timeout < 1:
        p.error('timeout must be positive')
    if a.sample and not a.manifest:
        p.error('--sample requires --manifest')
    if a.manifest and not a.manifest.is_file():
        p.error('模型清单不存在；先准备本地模型清单，或不带--manifest仅准备环境')
    if a.sample and not a.sample.is_file():
        p.error('样本音视频不存在')
    a.workspace = a.workspace.absolute()
    state = a.workspace/'.cantonese-subtitles'
    a.venv = (a.venv or state/'runtime').absolute()
    progress = Progress(state/'setup-progress.json')
    logs = state/'logs'
    # Only this installer and its children use this temporary directory.
    temp = state/'tmp'
    temp.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, TMPDIR=str(temp), TEMP=str(temp), TMP=str(temp),
               PYTHONUNBUFFERED='1', PIP_DISABLE_PIP_VERSION_CHECK='1')
    for key in ['PYTHONPATH', 'PYTHONHOME', 'PIP_EXTRA_INDEX_URL', 'PIP_INDEX_URL', 'PIP_TRUSTED_HOST']:
        env.pop(key, None)
    # Explicit index and isolated configuration for installs; no global pip config writes.
    env['PIP_CONFIG_FILE'] = os.devnull
    selected = None
    report = None
    try:
        with SetupLock(state/'setup.lock'):
            progress.set('setup', 'running', '检查环境并准备缺失部分')
            missing_tools = [tool for tool in ['ffmpeg', 'ffprobe'] if not shutil.which(tool)]
            if missing_tools:
                raise RuntimeError('缺少媒体工具，请先按系统正常方式安装：'+', '.join(missing_tools))
            if not a.fresh:
                for i, candidate in enumerate(candidates(a, state)):
                    path = state/f'candidate-{i}.local.json'
                    try:
                        run_logged([candidate, HERE/'check_runtime.py', '--workspace', state,
                                    '--report', path], progress, f'check_candidate_{i}', logs,
                                   env=env, timeout=min(a.timeout, 240))
                        report = json.loads(path.read_text())
                        selected = candidate
                        progress.set('environment_reuse', 'passed', f'复用验证通过的解释器：{selected}')
                        break
                    except (RuntimeError, TimeoutError, OSError):
                        progress.set(f'check_candidate_{i}', 'review', '此候选未通过自检；保留原环境，检查其他候选或创建专用环境')
                        progress.set('environment_reuse', 'review', f'候选环境检查未通过：{candidate}；不修改此环境')
            if selected is None:
                if a.offline:
                    raise RuntimeError('离线模式没有找到通过自检的环境；未下载或修改依赖')
                if a.fresh and a.venv.exists():
                    raise RuntimeError('--fresh要求全新的环境路径；不会覆盖已有目录')
                marker = a.venv/'.cantonese-subtitles-managed'
                if a.venv.exists() and not marker.exists():
                    raise RuntimeError(f'目标环境不是本脚本管理的目录，不作修改：{a.venv}')
                if not a.venv.exists():
                    run_logged([a.base_python, '-m', 'venv', a.venv], progress, 'create_environment', logs,
                               env=env, timeout=120)
                    marker.write_text('Created by setup_runtime.py\n')
                selected = interpreter(a.venv)
                source = a.index_url
                if source == 'auto':
                    progress.set('download_source', 'running', '检查清华镜像可达性 不保证下载速度')
                    try:
                        # Test using pip's own TLS/index behavior, rather than
                        # urllib with a possibly different macOS certificate store.
                        probe = subprocess.run([selected, '-m', 'pip', 'index', 'versions', 'av',
                                                '--index-url', TUNA, '--timeout', '10', '--retries', '0'],
                                               env=env, capture_output=True, text=True, timeout=30)
                        logs.mkdir(parents=True, exist_ok=True)
                        (logs/'mirror-check.log').write_text(probe.stdout+probe.stderr, encoding='utf-8')
                        if probe.returncode or '17.0.0' not in probe.stdout:
                            raise OSError('镜像不可达或没有所需PyAV版本，详见mirror-check.log')
                        source = TUNA
                    except Exception as e:
                        source = PYPI
                        progress.set('download_source', 'review', f'镜像检查失败，使用官方源；{type(e).__name__}')
                source = index_url(source)
                progress.set('download_source', 'passed', f'本次依赖索引：{urlsplit(source).hostname}；实际文件源见安装日志')
                base = [selected, '-m', 'pip', 'install', '-c', HERE/'requirements.txt', '--index-url', source,
                        '--progress-bar', 'on', '--prefer-binary', '--retries', '1', '--timeout', '30']
                # sox's source build needs NumPy present. Build this one known pure
                # Python wrapper after installing its build prerequisites.
                stages = [
                    ('build_tools', ['pip==26.2.2', 'setuptools==80.9.0', 'wheel==0.45.1', 'numpy==2.3.5'], ['--only-binary=:all:']),
                    ('whisper_dependencies', ['av==17.0.0', 'faster-whisper==1.2.1',
                                              'opencc-python-reimplemented==0.1.7', 'Pillow==12.0.0'],
                     ['--only-binary=av,numpy,ctranslate2,onnxruntime']),
                    ('sox_dependency', ['sox==1.5.0'], ['--no-build-isolation', '--no-deps']),
                ]
                for step, packages, options in stages:
                    run_logged(base+options+packages, progress, step, logs, env=env, timeout=a.timeout)
                if platform.system() != 'Darwin':
                    # CPU wheel avoids pulling a multi-gigabyte NVIDIA runtime on
                    # machines that have not explicitly selected CUDA.
                    run_logged([selected, '-m', 'pip', 'install', '--index-url',
                                'https://download.pytorch.org/whl/cpu', '--only-binary=:all:',
                                '--no-deps', '--retries', '1', '--timeout', '30', 'torch==2.10.0'],
                               progress, 'torch_cpu', logs, env=env, timeout=a.timeout)
                run_logged(base+['--only-binary=av,torch,numpy,nagisa,DyNet38,tokenizers',
                                 '-r', HERE/'requirements.txt'], progress, 'qwen_dependencies', logs,
                           env=env, timeout=a.timeout)
                run_logged([selected, '-m', 'pip', 'check'], progress, 'dependency_consistency', logs,
                           env=env, timeout=120)
                path = state/'runtime-check.local.json'
                run_logged([selected, HERE/'check_runtime.py', '--workspace', state, '--report', path],
                           progress, 'runtime_self_check', logs, env=env, timeout=240)
                report = json.loads(path.read_text())
            record = {**report, 'models': {}, 'model_load_verified': False,
                      'sample_inference_verified': False, 'saved_at': now()}
            if a.manifest:
                record['models'] = json.loads(a.manifest.read_text())
                device = a.qwen_device
                if device == 'auto':
                    devices = report.get('devices', {})
                    device = 'mps' if devices.get('mps') else 'cuda' if devices.get('cuda') else 'cpu'
                command = [selected, HERE/'verify_models.py', '--manifest', a.manifest,
                           '--workspace', a.workspace, '--qwen-device', device, '--timeout', str(a.timeout)]
                if a.sample:
                    command += ['--sample', a.sample]
                run_logged(command, progress, 'model_verification', logs, env=env, timeout=2*a.timeout+120)
                verified = json.loads((state/'models-check.local.json').read_text())
                record.update(model_load_verified=verified['model_load_verified'],
                              sample_inference_verified=verified['sample_inference_verified'])
            else:
                progress.set('model_verification', 'waiting', '环境检查通过；未提供模型清单，尚未验证模型加载或语音识别')
            atomic_json(state/'environment.local.json', record)
            if a.share:
                shared = Path.home()/'.config/cantonese-subtitles/environment.local.json'
                prior = json.loads(shared.read_text()) if shared.exists() else {}
                atomic_json(shared, {**prior, **record})
            progress.set('setup', 'passed', f'运行环境自检通过：{selected}；模型验证范围见记录')
            print(json.dumps({'python_executable': str(selected), 'environment_record':
                              str(state/'environment.local.json'), 'model_load_verified': record['model_load_verified'],
                              'sample_inference_verified': record['sample_inference_verified']}, ensure_ascii=False), flush=True)
    except Exception as e:
        progress.set('setup', 'failed', str(e))
        return 1
    except KeyboardInterrupt:
        progress.set('setup', 'waiting', '已中断；保留完成部分和日志，继续前核对状态')
        return 130
    return 0


if __name__ == '__main__':
    if hasattr(signal, 'SIGTERM'):
        def interrupted(*_):
            raise KeyboardInterrupt()
        signal.signal(signal.SIGTERM, interrupted)
    sys.exit(main())
