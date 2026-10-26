"""Download public model files only; never upload user media."""
import json
from pathlib import Path
from huggingface_hub import snapshot_download
from runtime_common import atomic_json


def validate(path, name):
    path = Path(path)
    required = ['config.json', 'model.bin', 'tokenizer.json'] if name == 'whisper' else [
        'config.json', 'tokenizer_config.json', 'preprocessor_config.json']
    if any(not (path / file).is_file() for file in required):
        raise RuntimeError(f'{name}: missing model configuration/weights/tokenizer')
    index = path / 'model.safetensors.index.json'
    if index.exists():
        shards = set(json.loads(index.read_text())['weight_map'].values())
        if any(not (path / shard).is_file() for shard in shards):
            raise RuntimeError(f'{name}: incomplete weight shards')
    elif not any(path.glob('*.safetensors')) and not (path / 'model.bin').is_file():
        raise RuntimeError(f'{name}: missing weights')


def installed_models(names):
    """Find complete model directories recorded by the offline installer."""
    try:
        record = json.loads((Path.home()/'.config/cantonese-subtitles/environment.local.json').read_text())
        if record.get('models_manifest'):
            models = json.loads(Path(record['models_manifest']).read_text())
        else:
            models = record.get('models', {})
        for name in names:
            validate(models[name]['path'], name)
        return {name: {**models[name], 'load_verified': False} for name in names}
    except (OSError, ValueError, KeyError, TypeError, RuntimeError):
        return None

if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--manifest', type=Path, required=True)
    p.add_argument('--offline', action='store_true', help='Resolve existing local cache only; no downloads')
    p.add_argument('--with-aligner', action='store_true', help='Optional forced aligner, not needed for text cross-checking')
    a = p.parse_args()
    names = ['whisper', 'qwen'] + (['aligner'] if a.with_aligner else [])
    if a.manifest.exists():
        existing = json.loads(a.manifest.read_text())
        for name in ['whisper', 'qwen'] + (['aligner'] if a.with_aligner else []):
            validate(existing[name]['path'], name)
        print('Reused local model manifest; files checked, loading not verified in this invocation.')
        print(a.manifest.resolve())
        raise SystemExit(0)
    installed = installed_models(names)
    if installed:
        atomic_json(a.manifest, installed)
        print('Reused models from the local installer; no network access or model download.')
        print(a.manifest.resolve())
        raise SystemExit(0)
    repos = {
        'whisper': 'Systran/faster-whisper-large-v3',
        'qwen': 'Qwen/Qwen3-ASR-1.7B',
    }
    if a.with_aligner:
        repos['aligner'] = 'Qwen/Qwen3-ForcedAligner-0.6B'
    manifest = {}
    for name, repo in repos.items():
        print(f'Preparing {name}: {repo}; offline={a.offline}', flush=True)
        path = Path(snapshot_download(repo, local_files_only=a.offline, allow_patterns=[
            '*.json', '*.safetensors', '*.txt', '*.bin', '*.jinja', '*.model']))
        validate(path, name)
        manifest[name] = {'repo': repo, 'path': str(path), 'snapshot': path.name,
                          'load_verified': False}
    atomic_json(a.manifest, manifest)
    print(a.manifest.resolve())
    print('Downloads checked; actual model loading still needs verification.')
