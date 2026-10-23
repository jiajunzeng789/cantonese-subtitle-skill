"""Resumable dual-model window runner; preserve raw overlap alternatives for editorial review."""
import argparse
from difflib import SequenceMatcher
import hashlib
import json
import math
import os
import signal
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace
import unicodedata
from runtime_common import Progress, SetupLock, atomic_json, run_logged

HERE = Path(__file__).resolve().parent


def windows(duration, size, overlap):
    if not all(math.isfinite(x) for x in [duration, size, overlap]):
        raise ValueError('时间必须为有限值')
    if duration <= 0 or not 0 < size <= 180 or not 0 <= overlap < size/2:
        raise ValueError('窗口须在180秒内，重叠须小于半个窗口，素材时长须为正')
    rows, start = [], 0.0
    while start < duration:
        end = min(duration, start+size)
        rows.append({'index': len(rows), 'start': round(start, 6), 'end': round(end, 6)})
        if end >= duration:
            break
        start += size-overlap
    return rows


def fingerprint(path):
    path = Path(path).resolve()
    stat = path.stat()
    return {'path': str(path), 'size': stat.st_size, 'mtime_ns': stat.st_mtime_ns}


def shift_segment(segment, offset, window_index):
    row = {**segment, 'start': segment['start']+offset, 'end': segment['end']+offset,
           'source_windows': [window_index]}
    row['words'] = [{**w, 'start': w['start']+offset, 'end': w['end']+offset}
                    for w in segment.get('words', [])]
    return row


def normalized(text):
    return ''.join(c for c in unicodedata.normalize('NFKC', text)
                   if not c.isspace() and unicodedata.category(c)[0] not in 'PS')


def merge_segments(items):
    """Only deduplicate matching text AND nearly identical audio intervals."""
    merged, removed = [], []
    for row in sorted(items, key=lambda r: (r['start'], r['end'])):
        duplicate = None
        for previous in reversed(merged):
            if previous['end'] < row['start']-1:
                # Earlier long segments may extend beyond this short one.
                continue
            if abs(previous['source_windows'][-1]-row['source_windows'][0]) != 1:
                continue
            text = normalized(row['text'])
            if len(text) >= 4 and text == normalized(previous['text']) and (
                    abs(row['start']-previous['start']) <= .25 and abs(row['end']-previous['end']) <= .25):
                duplicate = previous
                break
        if duplicate is None:
            merged.append(row)
        else:
            duplicate['source_windows'].extend(row['source_windows'])
            removed.append({'kept': duplicate['source_windows'][0], 'duplicate': row['source_windows'][0],
                            'start': row['start'], 'end': row['end']})
    return merged, removed


def worker(root, engine):
    from transcribe import process
    run = json.loads((root/'run.local.json').read_text())
    progress = Progress(root/f'{engine}-windows.progress.json')
    cache = {}
    for window in run['windows']:
        i = window['index']
        media = root/'windows'/f'window-{i:04d}.wav'
        output = root/'raw'/f'{engine}-{i:04d}.json'
        model = Path(run['models'][engine]['path'])
        if output.exists():
            try:
                old = json.loads(output.read_text())
                if (old['engine'] == engine and old['input'] == str(media.resolve())
                        and old['model'] == str(model.resolve()) and old['inference_executed']
                        and abs(old['audio_duration']-(window['end']-window['start'])) < .1):
                    progress.set(f'window_{i:04d}', 'passed', f'复用本次素材已完成结果 {i+1}/{len(run["windows"])}')
                    continue
            except (ValueError, KeyError):
                pass
            output.rename(output.with_name(output.name+f'.invalid-{time.time_ns()}'))
            progress.set(f'window_{i:04d}', 'review', '旧结果不完整或不匹配，已另存后重跑')
        progress.set(f'window_{i:04d}', 'running', f'{engine}窗口{i+1}/{len(run["windows"])} 来源{window["start"]:.2f}–{window["end"]:.2f}秒')
        args = SimpleNamespace(media=media, model=model, aligner=None, output=output,
                               engine=engine, device=run['qwen_device'] if engine == 'qwen' else 'cpu',
                               load_only=False, context='')
        try:
            process(args, Progress(output.with_suffix('.progress.json')), model_cache=cache)
            progress.set(f'window_{i:04d}', 'passed', f'{engine}窗口{i+1}/{len(run["windows"])}已保存')
        except Exception as e:
            progress.set(f'window_{i:04d}', 'failed', str(e))
            raise


def collect(root, run):
    segments, qwen, boundaries = [], [], []
    for window in run['windows']:
        i, offset = window['index'], window['start']
        w = json.loads((root/'raw'/f'whisper-{i:04d}.json').read_text())
        q = json.loads((root/'raw'/f'qwen-{i:04d}.json').read_text())
        segments.extend(shift_segment(s, offset, i) for s in w.get('segments', []))
        qwen.append({**window, 'text': q['text'], 'raw_path': str(root/'raw'/f'qwen-{i:04d}.json')})
        if i:
            previous = run['windows'][i-1]
            if previous['end'] > offset:
                matcher = SequenceMatcher(None, normalized(qwen[-2]['text'])[-160:], normalized(q['text'])[:160], autojunk=False)
                match = matcher.find_longest_match()
                candidate = normalized(qwen[-2]['text'])[-160:][match.a:match.a+match.size] if match.size >= 6 else None
                boundaries.append({'start': offset, 'end': previous['end'], 'windows': [i-1, i],
                                   'qwen_repetition_candidate': candidate, 'status': 'review',
                                   'reason': '重叠区可能有重复、截断或不同识别；需对照Whisper原始词时间与原音频校准'})
    merged, removed = merge_segments(segments)
    result = {'input': run['input'], 'audio_duration': run['audio_duration'],
              'media_metadata': run['media_metadata'], 'windows': run['windows'],
              'whisper_segments': merged, 'qwen_windows': qwen, 'exact_duplicates_removed': removed,
              'boundary_reviews': boundaries,
              'timestamp_origin': 'first decoded audio sample; original container metadata retained; verify media offset',
              'synchronization_verified': False, 'editorial_review_required': True}
    atomic_json(root/'combined.raw.json', result)
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('media', type=Path, nargs='?')
    p.add_argument('--manifest', type=Path)
    p.add_argument('--output-dir', type=Path, required=True)
    p.add_argument('--window-seconds', type=float, default=120)
    p.add_argument('--overlap-seconds', type=float, default=8)
    p.add_argument('--qwen-device', choices=['auto', 'cpu', 'mps', 'cuda'], default='auto')
    p.add_argument('--timeout-per-engine', type=int, default=7200)
    p.add_argument('--worker', choices=['whisper', 'qwen'], help=argparse.SUPPRESS)
    a = p.parse_args()
    if a.timeout_per_engine < 1:
        p.error('timeout-per-engine必须为正数')
    root = a.output_dir.absolute()
    if a.worker:
        worker(root, a.worker)
        return
    if not a.media or not a.media.is_file() or not a.manifest or not a.manifest.is_file():
        p.error('需要真实素材、完整本地模型清单和独立输出目录')
    root.mkdir(parents=True, exist_ok=True)
    progress = Progress(root/'pipeline-progress.json')
    env = dict(os.environ, HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', PYTHONUNBUFFERED='1')
    try:
        with SetupLock(root/'pipeline.lock'):
            models = json.loads(a.manifest.read_text())
            for name in ['whisper', 'qwen']:
                models[name]['path'] = str(Path(models[name]['path']).resolve())
                if not Path(models[name]['path']).is_dir():
                    raise ValueError(f'模型目录不存在：{name}')
            signature = {'input': fingerprint(a.media), 'models': models,
                         'window_seconds': a.window_seconds, 'overlap_seconds': a.overlap_seconds,
                         'transcribe_version': hashlib.sha256((HERE/'transcribe.py').read_bytes()).hexdigest(),
                         'requirements_version': hashlib.sha256((HERE/'requirements.txt').read_bytes()).hexdigest()}
            record_path = root/'run.local.json'
            if record_path.exists():
                run = json.loads(record_path.read_text())
                if run['signature'] != signature:
                    raise ValueError('素材、模型、窗口或脚本版本已变化；请使用新的输出目录，不覆盖历史结果')
            else:
                # Work in audio-relative time. Preserve original metadata rather
                # than silently assuming audio and video container zeros match.
                meta = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-show_format',
                                                          '-show_streams', '-of', 'json', str(a.media.resolve())], text=True))
                run_logged(['ffmpeg', '-v', 'error', '-nostdin', '-y', '-i', a.media.resolve(),
                            '-map', '0:a:0', '-vn', '-ar', '16000', '-ac', '1',
                            '-c:a', 'pcm_s16le', root/'audio.wav'], progress, 'extract_audio', root/'logs', env=env, timeout=300)
                duration = float(subprocess.check_output(['ffprobe', '-v', 'error', '-show_entries',
                                                          'format=duration', '-of', 'default=nw=1:nk=1', str(root/'audio.wav')], text=True))
                rows = windows(duration, a.window_seconds, a.overlap_seconds)
                device = a.qwen_device
                if device == 'auto':
                    import torch
                    device = 'mps' if torch.backends.mps.is_available() else 'cuda' if torch.cuda.is_available() else 'cpu'
                run = {'signature': signature, 'input': fingerprint(a.media), 'models': models,
                       'audio_duration': duration, 'media_metadata': meta, 'windows': rows, 'qwen_device': device}
                atomic_json(record_path, run)
            if a.qwen_device != 'auto' and a.qwen_device != run['qwen_device']:
                raise ValueError('所选Qwen设备与此任务不一致；请使用新的输出目录')
            (root/'windows').mkdir(exist_ok=True)
            (root/'raw').mkdir(exist_ok=True)
            for window in run['windows']:
                output = root/'windows'/f'window-{window["index"]:04d}.wav'
                # An interrupted extraction is safely replaced, results are not.
                duration = window['end']-window['start']
                valid = False
                if output.exists():
                    try:
                        existing = float(subprocess.check_output(['ffprobe', '-v', 'error', '-show_entries',
                                                                  'format=duration', '-of', 'default=nw=1:nk=1', str(output)], text=True))
                        valid = abs(existing-duration) < .05
                    except (ValueError, subprocess.CalledProcessError):
                        pass
                if not valid:
                    run_logged(['ffmpeg', '-v', 'error', '-nostdin', '-y', '-ss', str(window['start']),
                                '-i', root/'audio.wav', '-t', str(duration), '-acodec', 'pcm_s16le', output],
                               progress, f'extract_window_{window["index"]:04d}', root/'logs', env=env, timeout=120)
            for engine in ['whisper', 'qwen']:
                run_logged([sys.executable, Path(__file__).resolve(), '--output-dir', root, '--worker', engine],
                           progress, f'{engine}_windows', root/'logs', env=env, timeout=a.timeout_per_engine)
            result = collect(root, run)
            progress.set('collect', 'review' if result['boundary_reviews'] else 'passed',
                         f'两模型结果已保存；{len(result["boundary_reviews"])}处接缝待核对，未生成或验收发布字幕')
            print(root/'combined.raw.json', flush=True)
    except Exception as e:
        progress.set('pipeline', 'failed', str(e))
        return 1
    except KeyboardInterrupt:
        progress.set('pipeline', 'waiting', '任务已中断，已停止子进程；保留完成窗口，核对后可继续')
        return 130
    return 0


if __name__ == '__main__':
    if hasattr(signal, 'SIGTERM'):
        def interrupted(*_):
            raise KeyboardInterrupt()
        signal.signal(signal.SIGTERM, interrupted)
    sys.exit(main())
