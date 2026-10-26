"""Locate original Cantonese clauses with local Whisper before display-end extension."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import unicodedata
from runtime_common import Progress, activate_local_tools, atomic_json


def norm(text):
    return ''.join(c for c in text if unicodedata.category(c)[0] in 'LN')


def groups(cues):
    output=[];current=[]
    for i,c in enumerate(cues):
        if c['end']-c['start']>26:raise ValueError('原话单元超过26秒；先按原声拆分语义，不按字数分配时间')
        if current and (c['end']-cues[current[0]]['start']>22 or c['start']-cues[current[-1]]['end']>4):
            output.append(current);current=[]
        current.append(i)
    if current:output.append(current)
    return output


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for arg in ['media','input','model','output']:p.add_argument('--'+arg,type=Path,required=True)
    a=p.parse_args()
    if a.output.exists():p.error('输出已存在；请另存，不覆盖旧稿')
    if not a.media.is_file() or not a.model.is_dir():p.error('素材及本地Whisper模型必须存在')
    activate_local_tools()
    os.environ.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',TOKENIZERS_PARALLELISM='false')
    data=json.loads(a.input.read_text());cues=data['cues']
    from export_srt import prepare_display
    checked={**data,'display_policy':{'mode':'original'}}
    prepare_display(checked)  # Validate source anchors without changing them.
    for c in cues:
        if not isinstance(c.get('original'),str) or not norm(c['original']):
            p.error('每条必须提供original原始粤语，不能拿改写后的书面语做对齐')
    plan=groups(cues);progress=Progress(a.output.with_suffix('.progress.json'))
    root=a.output.with_suffix('.alignment');root.mkdir(parents=True,exist_ok=True)
    started=time.monotonic()
    try:
        from faster_whisper.audio import decode_audio
        import stable_whisper
        audio=decode_audio(str(a.media),sampling_rate=16000)
        if abs(len(audio)/16000-float(data['media_duration']))>.1:
            raise ValueError('素材与工作稿时长不一致，先核对剪辑、变速及音轨偏移')
        progress.set('model','running','加载本地Whisper large-v3 CPU int8')
        model=stable_whisper.load_faster_whisper(str(a.model.resolve()),device='cpu',compute_type='int8',
                                                cpu_threads=min(6,os.cpu_count() or 1),local_files_only=True)
        progress.set('model','passed','模型已加载；对齐结果仍需检查')
        kwargs=dict(language='yue',original_split=True,regroup=False,suppress_silence=False,
                    remove_instant_words=False,token_step=0,word_dur_factor=None,max_word_dur=None,verbose=None)
        source=dict(path=str(a.media.resolve()),size=a.media.stat().st_size,mtime=a.media.stat().st_mtime_ns,
                    model=str(a.model.resolve()),script=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
        for number,indices in enumerate(plan):
            first,last=indices[0],indices[-1]
            left=max(0,float(cues[first]['start'])-.4);right=min(len(audio)/16000,float(cues[last]['end'])+.4)
            if first:left=max(left,(cues[first-1]['end']+cues[first]['start'])/2)
            if last+1<len(cues):right=min(right,(cues[last]['end']+cues[last+1]['start'])/2)
            text='\n'.join(cues[i]['original'] for i in indices)
            signature=hashlib.sha256(json.dumps(dict(source=source,left=left,right=right,text=text),ensure_ascii=False).encode()).hexdigest()
            path=root/f'window-{number:04d}.json'
            progress.set('align','running',f'窗口{number+1}/{len(plan)}')
            if path.exists():
                cached=json.loads(path.read_text())
                if cached['signature']!=signature:raise ValueError('续接窗口与当前原文/素材不符，请换新输出名')
                result=cached['result']
            else:
                r=model.align(audio[round(left*16000):round(right*16000)],text,**kwargs)
                result=r.to_dict();atomic_json(path,dict(signature=signature,result=result))
            if len(result['segments'])!=len(indices):raise ValueError('原文句数映射改变，不能按索引盲目导出')
            for i,segment in zip(indices,result['segments']):
                c=cues[i];old_start,old_end=c['start'],c['end']
                if norm(segment['text'])!=norm(c['original']):raise ValueError('粤语原文映射失败')
                start=round(segment['start']+left,3);end=round(segment['end']+left,3)
                review=[]
                if end<=start or end-start<.15 or abs(start-old_start)>1.2 or abs(end-old_end)>1.2:
                    lo=max(0,old_start-.5);hi=min(len(audio)/16000,old_end+.5)
                    retry=model.align(audio[round(lo*16000):round(hi*16000)],c['original'],**kwargs)
                    s=retry.segments[0] if len(retry.segments)==1 else None
                    if s and s.end>s.start and abs(s.start+lo-old_start)<=1.2 and abs(s.end+lo-old_end)<=1.2:
                        start=round(s.start+lo,3);end=round(s.end+lo,3)
                        review.append('窗口异常，已局部重对齐，仍待回听')
                    else:
                        start,end=old_start,old_end;review.append('重对齐未通过，保留输入候选，未视为修好')
                if abs(start-old_start)>.35 or abs(end-old_end)>.35:review.append('与原候选相差超过350ms，待回听')
                c.update(start=float(start),end=float(end),speech_start=float(start),speech_end=float(end),
                         alignment_input_start=old_start,alignment_input_end=old_end,alignment_review=review)
        # A conflicting adjacent proposal must not silently move either onset.
        for i in range(1,len(cues)):
            if cues[i]['start']<cues[i-1]['end']:
                for j in [i-1,i]:
                    c=cues[j];c.update(start=c['alignment_input_start'],end=c['alignment_input_end'],
                                     speech_start=c['alignment_input_start'],speech_end=c['alignment_input_end'])
                    c['alignment_review'].append('相邻候选冲突，保留输入时间待审')
        prepare_display({**data,'display_policy':{'mode':'original'}})
        data.update(alignment_method='Whisper large-v3 original Cantonese clauses / stable-ts 2.19.1',
                    synchronization_verified=False,alignment_elapsed_seconds=time.monotonic()-started)
        atomic_json(a.output,data)
        progress.set('align','passed','原话定位完成；再用export_srt.py生成连续显示SRT与预览时间JSON')
        print(a.output.resolve(),flush=True)
    except Exception as e:
        progress.set('align','failed',str(e));raise


if __name__=='__main__':main()
