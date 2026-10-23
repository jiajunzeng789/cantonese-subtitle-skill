"""Resolve final display geometry and a configurable single-line subtitle preset."""
import argparse
import json
import math
from pathlib import Path
import subprocess

PRESETS = {
    'landscape': {'preferred_width': [16, 20], 'max_width': 20},
    'portrait': {'preferred_width': [10, 14], 'max_width': 14},
    'square': {'preferred_width': [12, 16], 'max_width': 16},
}


def classify(width, height):
    ratio = width / height
    return 'landscape' if ratio >= 1.2 else 'portrait' if ratio <= .85 else 'square'


def geometry(metadata):
    streams = [s for s in metadata.get('streams', [])
               if s.get('codec_type') == 'video'
               and not s.get('disposition', {}).get('attached_pic')]
    if not streams:
        return None
    s = streams[0]
    w, h = float(s['width']), float(s['height'])
    sar = s.get('sample_aspect_ratio', '1:1')
    if sar and sar not in ('N/A', '0:1'):
        a, b = (float(v) for v in sar.split(':'))
        if a > 0 and b > 0:
            w *= a / b
    rotation = s.get('tags', {}).get('rotate', 0)
    for item in s.get('side_data_list', []):
        if 'rotation' in item:
            rotation = item['rotation']
            break
    theta = math.radians(float(rotation) % 360)
    display_w = abs(w * math.cos(theta)) + abs(h * math.sin(theta))
    display_h = abs(w * math.sin(theta)) + abs(h * math.cos(theta))
    return round(display_w, 3), round(display_h, 3)


def make_profile(metadata=None, layout=None, final_size=None, max_width=None):
    explicit_layout = layout is not None
    if layout is not None and layout not in PRESETS:
        raise ValueError('Unknown layout')
    detected = geometry(metadata or {})
    dimensions = final_size or detected
    if dimensions and (len(dimensions) != 2 or not all(math.isfinite(x) and x > 0 for x in dimensions)):
        raise ValueError('Display dimensions must be positive finite numbers')
    if layout is None:
        if dimensions is None:
            raise ValueError('No video display geometry: supply final layout or final size for audio-only input')
        layout = classify(*dimensions)
    profile = {
        'layout': layout, **PRESETS[layout], 'single_line': True,
        'punctuation': 'no_sentence_punctuation', 'max_reading_speed': 9,
        'safe_width_fraction': .8,
        'geometry_source': 'explicit_final_size' if final_size else 'source_video' if detected else 'explicit_layout_only',
        'layout_source': 'explicit' if explicit_layout else 'detected',
        'width_measurement': 'approximate_full_width_units',
    }
    if max_width is not None:
        if not math.isfinite(max_width) or max_width <= 0:
            raise ValueError('max_width must be positive and finite')
        profile['max_width'] = max_width
    if dimensions:
        profile['display_width'], profile['display_height'] = dimensions
    if final_size and classify(*final_size) != layout:
        profile['warning'] = 'Explicit layout differs from final display size; review preset'
    return profile


def probe(path):
    return json.loads(subprocess.check_output([
        'ffprobe', '-v', 'error', '-show_streams', '-of', 'json', str(path)], text=True))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--media', type=Path)
    p.add_argument('--layout', choices=list(PRESETS))
    p.add_argument('--final-size', help='Final edited canvas, e.g. 1080x1920; overrides source geometry')
    p.add_argument('--max-width', type=float)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    size = tuple(float(n) for n in a.final_size.lower().split('x')) if a.final_size else None
    result = make_profile(probe(a.media) if a.media else {}, a.layout, size, a.max_width)
    a.output.parent.mkdir(parents=True, exist_ok=True)
    with a.output.open('x', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(a.output.resolve())


if __name__ == '__main__':
    main()
