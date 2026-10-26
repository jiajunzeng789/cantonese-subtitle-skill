"""Local short-sample ASR with recorded decoding, loading and inference checks."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
from runtime_common import Progress, now, activate_local_tools


def qwen_load(a):
    import torch
    from qwen_asr import Qwen3ASRModel
    dtype = torch.float32 if a.device == 'cpu' else torch.float16
    options = {}
    if a.aligner:
        options.update(forced_aligner=str(a.aligner.resolve()),
                       forced_aligner_kwargs={'device_map': a.device, 'dtype': dtype})
    return Qwen3ASRModel.from_pretrained(
        str(a.model.resolve()), device_map=a.device, dtype=dtype,
        max_inference_batch_size=1, max_new_tokens=4096, **options)


def process(a, progress, model_cache=None):
    activate_local_tools()
    os.environ['HF_HUB_OFFLINE'] = '1'
    os.environ['TRANSFORMERS_OFFLINE'] = '1'
    os.environ['TOKENIZERS_PARALLELISM'] = 'false'
    started = time.monotonic()
    progress.set('media_probe', 'running', '读取音轨与原始容器偏移')
    meta = json.loads(subprocess.check_output([
        'ffprobe', '-v', 'error', '-show_format', '-show_streams', '-of', 'json', str(a.media)], text=True))
    progress.set('media_probe', 'passed', '原媒体流信息已保存')
    with tempfile.TemporaryDirectory(prefix='cantonese-asr-') as tmp:
        wav = Path(tmp)/'audio.wav'
        progress.set('audio_decode', 'running', '验证FFmpeg与PyAV实际解码')
        subprocess.run(['ffmpeg', '-v', 'error', '-nostdin', '-i', str(a.media),
                        '-map', '0:a:0', '-vn', '-ac', '1', '-ar', '16000',
                        '-c:a', 'pcm_s16le', str(wav)], check=True)
        duration = float(subprocess.check_output([
            'ffprobe', '-v', 'error', '-show_entries', 'format=duration',
            '-of', 'default=nw=1:nk=1', str(wav)], text=True))
        if duration > 180:
            raise ValueError('此入口支持180秒内样本；长片请按重叠窗口处理并记录偏移')
        from faster_whisper.audio import decode_audio
        audio = decode_audio(str(wav), sampling_rate=16000)
        if not len(audio):
            raise ValueError('解码没有得到音频样本')
        progress.set('audio_decode', 'passed', f'{duration:.3f}秒音频解码成功')
        progress.set('model_load', 'running', f'加载本地{a.engine}模型 设备{a.device}')
        key = (a.engine, str(a.model.resolve()), a.device, str(a.aligner) if a.aligner else None)
        if model_cache is not None and key in model_cache:
            model = model_cache[key]
            progress.set('model_load', 'running', '复用当前进程已经加载的模型，不重新读取权重')
        elif a.engine == 'whisper':
            from faster_whisper import WhisperModel
            model = WhisperModel(str(a.model.resolve()), device=a.device,
                                 compute_type='int8' if a.device == 'cpu' else 'float16',
                                 cpu_threads=min(6, os.cpu_count() or 1), local_files_only=True)
        else:
            model = qwen_load(a)
        if model_cache is not None:
            model_cache[key] = model
        progress.set('model_load', 'passed', '本地模型加载成功；尚不代表推理或同步通过')
        result = {'text': '', 'words': []}
        if a.load_only:
            progress.set('inference', 'waiting', '本次仅验证模型加载 未进行语音推理')
        else:
            progress.set('inference', 'running', f'{a.engine}开始识别')
            if a.engine == 'whisper':
                segments, info = model.transcribe(
                    audio, language='yue', task='transcribe', beam_size=5,
                    word_timestamps=True, vad_filter=True, temperature=0,
                    condition_on_previous_text=False, initial_prompt=a.context or None)
                raw = []
                for s in segments:
                    raw.append({'id': s.id, 'start': s.start, 'end': s.end, 'text': s.text,
                                'words': [{'start': w.start, 'end': w.end, 'text': w.word,
                                           'probability': w.probability} for w in (s.words or [])]})
                    progress.set('inference', 'running', f'Whisper已输出至音频{s.end:.2f}秒；不代表全片完成')
                result = {'text': ''.join(s['text'] for s in raw), 'segments': raw}
            else:
                from faster_whisper.vad import get_speech_timestamps, VadOptions
                speech = get_speech_timestamps(audio, VadOptions())
                if speech:
                    r = model.transcribe(audio=str(wav), language='Cantonese', context=a.context,
                                         return_time_stamps=bool(a.aligner))[0]
                    words = [{'text': w.text, 'start': w.start_time, 'end': w.end_time}
                             for w in (r.time_stamps.items if r.time_stamps else [])]
                    result = {'text': r.text, 'words': words}
                    if a.aligner and r.text.strip() and not words:
                        result['review_items'] = ['对齐没有返回词时间；不得据此生成精确时间轴']
                result['speech_regions'] = [{'start': s['start']/16000, 'end': s['end']/16000} for s in speech]
                result['timing_available'] = bool(result.get('words'))
            progress.set('inference', 'passed', '识别调用完成；文字准确性和时间轴仍待校准')
    result.update(engine=a.engine, model=str(a.model.resolve()),
                  aligner=str(a.aligner.resolve()) if a.aligner else None,
                  input=str(a.media), context=a.context, media_metadata=meta,
                  audio_duration=duration, elapsed_seconds=time.monotonic()-started,
                  audio_decode_verified=True, model_load_verified=True,
                  inference_executed=not a.load_only,
                  timestamp_origin='first decoded audio sample; verify video offset',
                  synchronization_verified=False, completed_at=now())
    a.output.parent.mkdir(parents=True, exist_ok=True)
    with a.output.open('x', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    progress.set('raw_output', 'passed', f'原始结果已保存：{a.output}')
    print(a.output.resolve(), flush=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('media', type=Path)
    p.add_argument('--engine', choices=['whisper', 'qwen'], required=True)
    p.add_argument('--model', type=Path, required=True)
    p.add_argument('--aligner', type=Path, help='Optional; without it Qwen outputs text for cross-checking')
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--progress', type=Path)
    p.add_argument('--load-only', action='store_true')
    p.add_argument('--context', default='')
    p.add_argument('--device', default='cpu', choices=['cpu', 'cuda', 'mps'])
    a = p.parse_args()
    if not a.media.is_file() or not a.model.is_dir():
        p.error('素材及完整本地模型目录必须存在')
    if a.aligner and not a.aligner.is_dir():
        p.error('指定的对齐模型目录不存在')
    if a.output.exists():
        p.error('输出已存在，请使用新的文件名')
    if a.engine == 'whisper' and a.device == 'mps':
        p.error('faster-whisper支持CPU/CUDA，不支持MPS')
    a.media = a.media.resolve()
    progress = Progress(a.progress or a.output.with_suffix('.progress.json'))
    try:
        process(a, progress)
    except Exception as e:
        active = next((x['id'] for x in reversed(progress.data['steps']) if x['status'] == 'running'), 'transcription')
        progress.set(active, 'failed', str(e))
        raise


if __name__ == '__main__':
    main()
