"""Export single-line SRT after semantic segmentation and source-audio timing."""
import argparse
import json
import math
from pathlib import Path
import re
import unicodedata
from subtitle_profile import make_profile

RESIDUE = re.compile(r'[佢唔冇嘅咗啲喺嚟噉嗰乜咩嘢畀]')
# Protect meaningful symbols, not sentence punctuation. Official tokens can
# also be provided explicitly, e.g. an unusual punctuated brand spelling.
TOKEN_PATTERNS = [
    re.compile(r'\d+(?:[.．]\d+)+'),
    re.compile(r'\d+(?::\d+)+'),
    re.compile(r'\d+(?:\.\d+)?\s*[%％‰°℃℉]'),
    re.compile(r'\d+(?:\.\d+)?[%％‰°℃℉]?\s*[-–—~～至]\s*\d+(?:\.\d+)?[%％‰°℃℉]?'),
    re.compile(r'[A-Za-z0-9]+(?:[-/][A-Za-z0-9]+)+'),
    re.compile(r'(?:[+−±<>≤≥≈﹣－-]\s*\d+(?:\.\d+)?|\d+(?:\.\d+)?(?:万|亿|千|百)?\s*[+])'),
    re.compile(r'\d+(?:\.\d+)?\s*[×÷*]\s*\d+(?:\.\d+)?'),
    re.compile(r'[A-Za-z0-9]+(?:[·・][A-Za-z0-9]+)+'),
]


def stamp(ms):
    hours, remainder = divmod(ms, 3600000)
    minutes, remainder = divmod(remainder, 60000)
    seconds, millis = divmod(remainder, 1000)
    return f'{hours:02}:{minutes:02}:{seconds:02},{millis:03}'


def width(text):
    return sum(1 if unicodedata.east_asian_width(c) in 'WF' else .5 for c in text)


def clean_text(text, protected_terms=()):
    if not isinstance(protected_terms, (list, tuple)):
        raise ValueError('protected_terms must be a list of official tokens')
    if re.search(r'[\r\n\u0085\u2028\u2029]', text):
        raise ValueError('Multi-line cue: resegment into single-line semantic units with audio timestamps')
    if any(ord(c) < 32 and c != '\t' for c in text):
        raise ValueError('Control character in cue')
    if re.search(r'<\s*/?\s*[A-Za-z][^>]*>|\{\\[^}]*\}', text):
        raise ValueError('Subtitle markup is not allowed in plain single-line output')
    from opencc import OpenCC
    converter = OpenCC('t2s')
    text = converter.convert(text).replace('．', '.').replace('％', '%')
    text = re.sub(r'(?<!\d)\d{1,3}(?:,\d{3})+(?!\d)', lambda m: m.group().replace(',', ''), text)
    keep = set()
    spans = [m.span() for pattern in TOKEN_PATTERNS for m in pattern.finditer(text)]
    for term in protected_terms:
        if (not isinstance(term, str) or not term.strip() or re.search(r'[\r\n]', term)
                or not any(unicodedata.category(c)[0] in ('L', 'N') for c in term)):
            raise ValueError('protected_terms must contain nonempty single-line official tokens')
        term = converter.convert(term).replace('．', '.').replace('％', '%')
        spans.extend(m.span() for m in re.finditer(re.escape(term), text))
    for start, end in spans:
        keep.update(range(start, end))
    chars = []
    for i, c in enumerate(text):
        if unicodedata.category(c)[0] in ('P', 'S') and i not in keep:
            chars.append(' ')
        else:
            chars.append(c)
    return re.sub(r'\s+', ' ', ''.join(chars)).strip()


def pixel_width(text, profile):
    if not profile.get('font_file'):
        return None
    from PIL import ImageFont
    size = int(profile['font_size_px'])
    if size <= 0:
        raise ValueError('font_size_px must be positive')
    font = ImageFont.truetype(profile['font_file'], size)
    tracking = float(profile.get('letter_spacing_px', 0))
    stroke = float(profile.get('stroke_width_px', 0))
    if not math.isfinite(tracking) or not math.isfinite(stroke) or stroke < 0:
        raise ValueError('Font spacing must be finite and stroke width nonnegative')
    return font.getlength(text) + max(0, len(text)-1)*tracking + 2*stroke


def render(data):
    cues = data['cues']
    if not isinstance(cues, list) or not cues:
        raise ValueError('cues must be a nonempty list')
    duration = float(data['media_duration'])
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError('media_duration must be positive and finite')
    incoming = data.get('subtitle_profile')
    if not isinstance(incoming, dict) or 'layout' not in incoming:
        raise ValueError('subtitle_profile.layout is required; detect video geometry or supply final layout for audio')
    if incoming.get('single_line') is False:
        raise ValueError('This exporter requires single_line=true')
    profile = {**make_profile(layout=incoming['layout']), **incoming}
    limit = float(profile['max_width'])
    cps = float(profile.get('max_reading_speed', 9))
    if not all(math.isfinite(v) and v > 0 for v in (limit, cps)):
        raise ValueError('Width and reading speed must be positive finite numbers')
    safe_fraction = float(profile.get('safe_width_fraction', .8))
    if not 0 < safe_fraction <= 1:
        raise ValueError('safe_width_fraction must be in (0, 1]')
    available = None
    if 'display_width' in profile:
        display_width = float(profile['display_width'])
        if not math.isfinite(display_width) or display_width <= 0:
            raise ValueError('display_width must be positive and finite')
        available = display_width * safe_fraction
    if profile.get('font_file') and (not available or not profile.get('font_size_px')):
        raise ValueError('Font measurement needs final display_width and font_size_px')
    if available and profile.get('font_size_px') and not profile.get('font_file'):
        font_size = float(profile['font_size_px'])
        if not math.isfinite(font_size) or font_size <= 0:
            raise ValueError('font_size_px must be positive and finite')
        limit = min(limit, available/font_size)
    previous_end = 0
    blocks, warnings = [], []
    for i, cue in enumerate(cues, 1):
        start, end = float(cue['start']), float(cue['end'])
        if not all(math.isfinite(v) for v in [start, end]):
            raise ValueError(f'cue {i}: non-finite time')
        start_ms, end_ms = round(start*1000), round(end*1000)
        if start < 0 or end > duration or end_ms <= start_ms or start_ms < previous_end:
            raise ValueError(f'cue {i}: negative/overlapping/out-of-range/empty time')
        if not isinstance(cue['text'], str):
            raise ValueError(f'cue {i}: text must be a string')
        text = clean_text(cue['text'], data.get('protected_terms', []))
        if not text:
            raise ValueError(f'cue {i}: empty after punctuation removal')
        if RESIDUE.search(text):
            raise ValueError(f'cue {i}: likely Cantonese residue')
        if width(text) > limit:
            raise ValueError(f'cue {i}: width {width(text):.1f} exceeds {limit:.1f}; resegment meaning and retime, do not wrap or truncate')
        measured = pixel_width(text, profile)
        if measured is not None and measured > available:
            raise ValueError(f'cue {i}: actual font width {measured:.1f}px exceeds safe width {available:.1f}px')
        if width(text)/(end-start) > cps:
            warnings.append(f'cue {i}: reading speed above {cps:g} full-width units/s')
        if end-start < 1 or end-start > 7:
            warnings.append(f'cue {i}: short/long display duration; review against audio')
        if '系' in text:
            warnings.append(f'cue {i}: inspect 系 context; 系统/关系/车系 are valid')
        blocks.append(f'{i}\n{stamp(start_ms)} --> {stamp(end_ms)}\n{text}\n')
        previous_end = end_ms
    if not profile.get('font_file'):
        warnings.append('Width is an estimate; match font, size and safe area in the editor and preview automatic wrapping')
    return '\n'.join(blocks), warnings


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('input', type=Path)
    p.add_argument('output', type=Path)
    p.add_argument('--profile', type=Path, help='JSON from subtitle_profile.py; overrides input subtitle_profile')
    p.add_argument('--layout', choices=['landscape', 'portrait', 'square'], help='Explicit final layout, overrides profile')
    a = p.parse_args()
    if a.output.suffix.lower() != '.srt':
        p.error('Output must end in .srt')
    data = json.loads(a.input.read_text(encoding='utf-8'))
    if a.profile:
        data['subtitle_profile'] = json.loads(a.profile.read_text(encoding='utf-8'))
    if a.layout:
        # A new final layout must not inherit a stale source canvas/font fit.
        data['subtitle_profile'] = make_profile(layout=a.layout)
    body, warnings = render(data)
    a.output.parent.mkdir(parents=True, exist_ok=True)
    with a.output.open('x', encoding='utf-8', newline='\n') as f:
        f.write(body)
    for warning in warnings:
        print('REVIEW:', warning)
    print('Single-line SRT exported. Meaning, speaker turns and synchronization still need review.')
    print(a.output.resolve())


if __name__ == '__main__':
    main()
