"""Download public model files only; never upload user media."""
import json
from pathlib import Path
from huggingface_hub import snapshot_download

if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--manifest', type=Path, required=True)
    a = p.parse_args()
    if a.manifest.exists():
        p.error('Manifest exists; choose a new output path.')
    repos = {
        'whisper': 'Systran/faster-whisper-large-v3',
        'qwen': 'Qwen/Qwen3-ASR-1.7B',
        'aligner': 'Qwen/Qwen3-ForcedAligner-0.6B',
    }
    manifest = {}
    for name, repo in repos.items():
        path = Path(snapshot_download(repo, allow_patterns=[
            '*.json', '*.safetensors', '*.txt', '*.bin']))
        index = path / 'model.safetensors.index.json'
        if index.exists():
            shards = set(json.loads(index.read_text())['weight_map'].values())
            if any(not (path / shard).is_file() for shard in shards):
                raise RuntimeError(f'{repo}: incomplete weight shards')
        elif not any(path.glob('*.safetensors')) and not (path / 'model.bin').is_file():
            raise RuntimeError(f'{repo}: missing weights')
        manifest[name] = {'repo': repo, 'path': str(path), 'snapshot': path.name,
                          'load_verified': False}
    a.manifest.parent.mkdir(parents=True, exist_ok=True)
    with a.manifest.open('x', encoding='utf-8') as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    print(a.manifest.resolve())
    print('Downloads checked; actual model loading still needs verification.')
