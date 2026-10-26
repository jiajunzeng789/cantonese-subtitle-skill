"""Verify versions, imports, FFmpeg, and real faster-whisper audio decoding."""
import argparse
from importlib.metadata import version
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import traceback
import wave
from runtime_common import atomic_json, classify_error, now, activate_local_tools


def check(workspace):
    activate_local_tools()
    workspace = Path(workspace)
    workspace.mkdir(parents=True, exist_ok=True)
    out = {'python_executable': sys.executable, 'python_version': platform.python_version(),
           'platform': platform.platform(), 'checked_at': now(), 'checks': {}, 'package_versions': {}}

    def stage(name, operation):
        print(f'🔄 自检 {name}', flush=True)
        try:
            evidence = operation()
            out['checks'][name] = {'status': 'passed', 'evidence': evidence}
            print(f'✅ 自检 {name} 通过', flush=True)
        except Exception as e:
            out['checks'][name] = {'status': 'failed', 'error': str(e),
                                   'category': classify_error(str(e)), 'traceback': traceback.format_exc()}
            print(f'❌ 自检 {name}: {e}', flush=True)

    def versions():
        for row in (Path(__file__).with_name('requirements.txt')).read_text().splitlines():
            if not row.strip() or row.startswith('#'):
                continue
            name, expected = row.split('==')
            actual = version(name)
            out['package_versions'][name] = actual
            if actual.split('+')[0] != expected:
                raise RuntimeError(f'{name}={actual}，此基线要求{expected}')
        return out['package_versions']

    def imports():
        import faster_whisper
        import qwen_asr
        import torch
        from opencc import OpenCC
        from PIL import ImageFont
        assert OpenCC('t2s').convert('廣東') == '广东'
        out['devices'] = {'cuda': torch.cuda.is_available(), 'mps': torch.backends.mps.is_available()}
        return 'Whisper/Qwen/Torch/OpenCC/Pillow导入成功'

    def media_tools():
        tools = {}
        for tool in ['ffmpeg', 'ffprobe']:
            path = shutil.which(tool)
            if not path:
                raise RuntimeError(f'缺少{tool}；请先安装系统媒体工具')
            subprocess.run([path, '-version'], check=True, capture_output=True, timeout=15)
            tools[tool] = path
        return tools

    def decode():
        from faster_whisper.audio import decode_audio
        import numpy as np
        with tempfile.TemporaryDirectory(dir=workspace, prefix='decode-check-') as t:
            source, target = Path(t)/'source.wav', Path(t)/'decoded.wav'
            with wave.open(str(source), 'wb') as f:
                f.setnchannels(1)
                f.setsampwidth(2)
                f.setframerate(16000)
                f.writeframes(b'\0\0'*16000)
            subprocess.run(['ffmpeg', '-v', 'error', '-nostdin', '-i', str(source),
                            '-ac', '1', '-ar', '16000', str(target)],
                           check=True, capture_output=True, timeout=30)
            audio = decode_audio(str(target), sampling_rate=16000)
            if len(audio) != 16000 or not np.isfinite(audio).all():
                raise RuntimeError('解码样本长度或数值异常')
        return 'FFmpeg转换及faster-whisper/PyAV实际解码16000样本通过；未进行语音推理'

    stage('versions', versions)
    stage('imports', imports)
    stage('media_tools', media_tools)
    stage('audio_decode', decode)
    out['passed'] = all(x['status'] == 'passed' for x in out['checks'].values())
    return out


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--workspace', type=Path, required=True)
    p.add_argument('--report', type=Path, required=True)
    a = p.parse_args()
    result = check(a.workspace)
    atomic_json(a.report, result)
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    sys.exit(0 if result['passed'] else 1)
