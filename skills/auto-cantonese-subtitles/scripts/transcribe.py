"""Local short-sample ASR; raw output only, no editorial claims."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('media', type=Path)
    p.add_argument('--engine', choices=['whisper', 'qwen'], required=True)
    p.add_argument('--model', type=Path, required=True)
    p.add_argument('--aligner', type=Path)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--context', default='')
    p.add_argument('--device', default='cpu', choices=['cpu', 'cuda', 'mps'])
    a = p.parse_args()
    if not a.media.is_file() or not a.model.is_dir():
        p.error('Media file and complete local model directory are required.')
    if a.output.exists():
        p.error('Output already exists; use a new path.')
    if a.engine == 'qwen' and (not a.aligner or not a.aligner.is_dir()):
        p.error('Qwen requires a complete local forced-aligner directory.')
    if a.engine == 'whisper' and a.device == 'mps':
        p.error('faster-whisper supports CPU/CUDA, not MPS.')
    os.environ['HF_HUB_OFFLINE'] = '1'
    os.environ['TRANSFORMERS_OFFLINE'] = '1'
    a.media = a.media.resolve()
    # Keep original offsets available for diagnosing stream/container timing.
    meta = json.loads(subprocess.check_output([
        'ffprobe', '-v', 'error', '-show_format', '-show_streams',
        '-of', 'json', str(a.media)], text=True))
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix='cantonese-asr-') as tmp:
        wav = Path(tmp) / 'audio.wav'
        # Audio time zero here is its first decoded sample, not necessarily
        # video container zero; retained metadata must be used by the editor.
        subprocess.run(['ffmpeg', '-v', 'error', '-nostdin', '-i', str(a.media),
                        '-map', '0:a:0', '-vn', '-ac', '1', '-ar', '16000',
                        '-c:a', 'pcm_s16le', str(wav)], check=True)
        duration = float(subprocess.check_output([
            'ffprobe', '-v', 'error', '-show_entries', 'format=duration',
            '-of', 'default=nw=1:nk=1', str(wav)], text=True))
        if duration > 180:
            p.error('This first test runner accepts samples up to 180 seconds.')
        if a.engine == 'whisper':
            from faster_whisper import WhisperModel
            model = WhisperModel(str(a.model.resolve()), device=a.device,
                                 compute_type='int8' if a.device == 'cpu' else 'float16',
                                 local_files_only=True)
            segments, info = model.transcribe(
                str(wav), language='yue', task='transcribe', beam_size=5,
                word_timestamps=True, vad_filter=True,
                initial_prompt=a.context or None)
            raw = [{'id': s.id, 'start': s.start, 'end': s.end, 'text': s.text,
                    'words': [{'start': w.start, 'end': w.end, 'text': w.word,
                               'probability': w.probability} for w in (s.words or [])]}
                   for s in segments]
            result = {'text': ''.join(s['text'] for s in raw), 'segments': raw}
        else:
            from faster_whisper.audio import decode_audio
            from faster_whisper.vad import get_speech_timestamps, VadOptions
            speech = get_speech_timestamps(decode_audio(str(wav)), VadOptions())
            if not speech:
                result = {'text': '', 'words': [], 'speech_regions': []}
            else:
                result = qwen_transcribe(a, wav)
                result['speech_regions'] = [
                    {'start': s['start']/16000, 'end': s['end']/16000} for s in speech]
    result.update(engine=a.engine, model=str(a.model.resolve()),
                  aligner=str(a.aligner.resolve()) if a.aligner else None,
                  input=str(a.media), context=a.context, media_metadata=meta,
                  audio_duration=duration, elapsed_seconds=time.monotonic()-started,
                  timestamp_origin='first decoded audio sample; verify video offset',
                  synchronization_verified=False)
    a.output.parent.mkdir(parents=True, exist_ok=True)
    with a.output.open('x', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(a.output.resolve())


def qwen_transcribe(a, wav):
    import torch
    from qwen_asr import Qwen3ASRModel
    dtype = torch.float32 if a.device == 'cpu' else torch.float16
    model = Qwen3ASRModel.from_pretrained(
        str(a.model.resolve()), device_map=a.device, dtype=dtype,
        forced_aligner=str(a.aligner.resolve()),
        forced_aligner_kwargs={'device_map': a.device, 'dtype': dtype},
        max_inference_batch_size=1, max_new_tokens=4096)
    r = model.transcribe(audio=str(wav), language='Cantonese',
                         context=a.context, return_time_stamps=True)[0]
    if r.text.strip() and r.time_stamps is None:
        raise RuntimeError('Speech was recognized without alignment; cannot report timed output.')
    words = [{'text': w.text, 'start': w.start_time, 'end': w.end_time}
             for w in (r.time_stamps.items if r.time_stamps else [])]
    return {'text': r.text, 'words': words}

if __name__ == '__main__':
    main()
