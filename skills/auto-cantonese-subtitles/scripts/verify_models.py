"""Verify each model locally and optionally run a short sample; never invent SRT text."""
import argparse
import json
import os
from pathlib import Path
import sys
import time
import wave
from runtime_common import Progress, SetupLock, atomic_json, run_logged


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--manifest', type=Path, required=True)
    p.add_argument('--workspace', type=Path, required=True)
    p.add_argument('--sample', type=Path)
    p.add_argument('--sample-seconds', type=float, default=12)
    p.add_argument('--qwen-device', choices=['cpu', 'mps', 'cuda'], default='cpu')
    p.add_argument('--timeout', type=int, default=1200)
    a = p.parse_args()
    if not 0 < a.sample_seconds <= 60:
        p.error('self-check sample must be 0–60 seconds')
    state = a.workspace.absolute()/'.cantonese-subtitles'
    progress = Progress(state/'model-progress.json')
    root = state/'verification'/str(time.time_ns())
    root.mkdir(parents=True)
    env = dict(os.environ, HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', PYTHONUNBUFFERED='1')
    evidence = {}
    progress.set('models', 'running', '验证本地模型清单与加载/推理范围')
    try:
        manifest = json.loads(a.manifest.read_text())
        for name in ['whisper', 'qwen']:
            if not Path(manifest[name]['path']).is_dir():
                raise ValueError(f'{name}本地模型目录不存在')
        with SetupLock(state/'verify-models.lock'):
            media = root/'sample.wav'
            if a.sample:
                if not a.sample.is_file():
                    raise ValueError('sample media does not exist')
                run_logged(['ffmpeg', '-v', 'error', '-nostdin', '-i', a.sample,
                            '-t', str(a.sample_seconds), '-map', '0:a:0', '-vn', '-ar', '16000',
                            '-ac', '1', '-c:a', 'pcm_s16le', media], progress, 'sample_extract',
                           state/'logs', env=env, timeout=120)
            else:
                with wave.open(str(media), 'wb') as f:
                    f.setnchannels(1)
                    f.setsampwidth(2)
                    f.setframerate(16000)
                    f.writeframes(b'\0\0'*16000)
            for name in ['whisper', 'qwen']:
                output = root/f'{name}.raw.json'
                cmd = [sys.executable, Path(__file__).with_name('transcribe.py'), media,
                       '--engine', name, '--model', manifest[name]['path'], '--device',
                       a.qwen_device if name == 'qwen' else 'cpu', '--output', output]
                if not a.sample:
                    cmd.append('--load-only')
                run_logged(cmd, progress, f'{name}_sample' if a.sample else f'{name}_load',
                           state/'logs', env=env, timeout=a.timeout)
                raw = json.loads(output.read_text())
                evidence[name] = {'model_load_verified': raw['model_load_verified'],
                                  'inference_executed': raw['inference_executed'],
                                  'nonempty_text': bool(raw['text'].strip()), 'output': str(output)}
                if a.sample and not raw['text'].strip():
                    raise RuntimeError(f'{name}样本识别为空，可能无语音或VAD漏检；不能认定语音测试通过')
            report = {'model_load_verified': True, 'sample_inference_verified': bool(a.sample),
                      'source_sample': str(a.sample.absolute()) if a.sample else None,
                      'maximum_sample_seconds': a.sample_seconds, 'models': evidence,
                      'synchronization_verified': False}
            atomic_json(state/'models-check.local.json', report)
            progress.set('models', 'passed', '双模型加载通过'+(' 且短样本均返回文本；准确性与同步未验收' if a.sample else ' 未执行语音推理'))
    except Exception as e:
        atomic_json(state/'models-check.local.json', {
            'model_load_verified': False, 'sample_inference_verified': False,
            'models': evidence, 'error': str(e)})
        progress.set('models', 'failed', str(e))
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
