import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from runtime_common import activate_local_tools
from prepare_models import installed_models
from realign_subtitles import groups


class OfflineToolsTests(unittest.TestCase):
    def test_alignment_windows_preserve_units_and_stop_at_long_gaps(self):
        cues=[{'start':0,'end':3},{'start':4,'end':7},{'start':13,'end':16},{'start':17,'end':30}]
        plan=groups(cues)
        self.assertEqual([i for group in plan for i in group],list(range(len(cues))))
        self.assertFalse(any(1 in group and 2 in group for group in plan))
        self.assertTrue(all(cues[g[-1]]['end']-cues[g[0]]['start']<=26 for g in plan))

    def test_alignment_never_splits_a_long_sentence_by_character_count(self):
        with self.assertRaises(ValueError):groups([{'start':0,'end':27}])

    def test_bundled_tools_are_process_local_and_idempotent(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);tools=root/'Media Tools/bin';tools.mkdir(parents=True)
            for name in ['ffmpeg','ffprobe']:
                f=tools/name;f.write_text('#!/bin/sh\nexit 0\n');f.chmod(0o755)
            with patch.dict(os.environ,{'CANTONESE_SUBTITLES_TOOLS':str(tools),'PATH':'/usr/bin:/bin'}):
                self.assertEqual(activate_local_tools(),tools)
                self.assertEqual(activate_local_tools(),tools)
                self.assertEqual(os.environ['PATH'],str(tools)+':/usr/bin:/bin')

    def test_installer_models_are_found_outside_huggingface_cache(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);models={}
            for name in ['whisper','qwen']:
                folder=root/name;folder.mkdir()
                files=['config.json','model.bin','tokenizer.json'] if name=='whisper' else [
                    'config.json','tokenizer_config.json','preprocessor_config.json','model.safetensors']
                for f in files:(folder/f).write_text('{}')
                models[name]={'path':str(folder),'repo':'test/'+name}
            manifest=root/'models.local.json';manifest.write_text(json.dumps(models))
            record=root/'.config/cantonese-subtitles/environment.local.json';record.parent.mkdir(parents=True)
            record.write_text(json.dumps({'models_manifest':str(manifest)}))
            with patch('pathlib.Path.home',return_value=root):
                result=installed_models(['whisper','qwen'])
                self.assertEqual(result['qwen']['path'],models['qwen']['path'])
                self.assertFalse(result['qwen']['load_verified'])
                (root/'qwen/model.safetensors').unlink()
                self.assertIsNone(installed_models(['whisper','qwen']))


if __name__=='__main__':unittest.main()
