"""Failure-path tests; model inference is covered by a separate real-media validation."""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import subprocess
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from runtime_common import Progress, SetupLock, atomic_json, classify_error, index_url, run_logged
from setup_runtime import candidates, interpreter
import setup_runtime
from transcribe_long import windows, shift_segment, merge_segments, worker
from unittest.mock import patch


class RuntimeTests(unittest.TestCase):
    def test_security_claim_requires_evidence(self):
        self.assertEqual(classify_error('OSError EEXIST mkdir /tmp/test'), 'temporary_directory_conflict')
        self.assertEqual(classify_error('Operation not permitted'), 'permission_denied')
        self.assertEqual(classify_error('TypeError unexpected keyword metadata_errors'), 'pyav_incompatible')

    def test_reject_credential_and_insecure_indexes(self):
        for url in ['http://example.org/simple', 'https://user:token@example.org/simple',
                    'https://example.org/simple?token=secret']:
            with self.assertRaises(ValueError):
                index_url(url)

    def test_lock_never_overwrites_existing_task(self):
        with tempfile.TemporaryDirectory() as t:
            lock = Path(t)/'setup.lock'
            with SetupLock(lock):
                original = lock.read_bytes()
                with self.assertRaises(RuntimeError):
                    with SetupLock(lock):
                        pass
                self.assertEqual(lock.read_bytes(), original)
            self.assertFalse(lock.exists())

    def test_real_failed_process_saved_as_failure(self):
        with tempfile.TemporaryDirectory() as t:
            progress = Progress(Path(t)/'progress.json')
            with self.assertRaises(RuntimeError):
                run_logged([sys.executable, '-c', 'print("EEXIST temporary directory");raise SystemExit(7)'],
                           progress, 'install', t, timeout=3)
            row = json.loads(progress.path.read_text())['steps'][0]
            self.assertEqual(row['status'], 'failed')
            self.assertEqual(row['exit_code'], 7)
            self.assertEqual(row['error']['category'], 'temporary_directory_conflict')
            self.assertTrue(Path(row['log_path']).is_file())

    def test_timeout_does_not_leave_running_state(self):
        with tempfile.TemporaryDirectory() as t:
            progress = Progress(Path(t)/'progress.json')
            with self.assertRaises(TimeoutError):
                run_logged([sys.executable, '-c', 'import time;time.sleep(30)'],
                           progress, 'blocked', t, timeout=.2, heartbeat=.1)
            self.assertEqual(progress.data['steps'][0]['status'], 'failed')

    @unittest.skipUnless(os.name == 'posix', 'venv symlink invariant on POSIX')
    def test_existing_venv_path_is_not_resolved_to_base_python(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            candidate = root/'venv/bin/python'
            candidate.parent.mkdir(parents=True)
            candidate.symlink_to(sys.executable)
            args = argparse.Namespace(python=candidate, venv=root/'other', workspace=root)
            self.assertEqual(candidates(args, root)[0], candidate)
            self.assertNotEqual(candidates(args, root)[0], candidate.resolve())

    def test_atomic_progress_preserves_existing_steps(self):
        with tempfile.TemporaryDirectory() as t:
            path = Path(t)/'progress.json'
            atomic_json(path, {'steps': [{'id': 'whisper', 'status': 'passed'}]})
            progress = Progress(path)
            progress.set('qwen', 'failed', 'failure evidence')
            saved = json.loads(path.read_text())
            self.assertEqual(saved['steps'][0]['status'], 'passed')
            self.assertEqual(saved['steps'][1]['status'], 'failed')

    def test_window_plan_has_no_gaps_and_obeys_limit(self):
        plan = windows(570.84, 120, 8)
        self.assertEqual(plan[0]['start'], 0)
        self.assertEqual(plan[-1]['end'], 570.84)
        for a, b in zip(plan, plan[1:]):
            self.assertAlmostEqual(a['end']-b['start'], 8)
        self.assertTrue(all(0 < w['end']-w['start'] <= 180 for w in plan))
        for args in [(100, 181, 8), (100, 10, 10), (float('inf'), 120, 8)]:
            with self.assertRaises(ValueError):
                windows(*args)

    def test_segment_and_words_retain_window_offset(self):
        original = {'start': 1, 'end': 3, 'text': '测试字幕',
                    'words': [{'start': 1, 'end': 2, 'text': '测试'}]}
        shifted = shift_segment(original, 112, 1)
        self.assertEqual(shifted['start'], 113)
        self.assertEqual(shifted['words'][0]['end'], 114)
        self.assertEqual(original['words'][0]['start'], 1)

    def test_legitimate_repeated_speech_not_removed_by_text_only(self):
        rows = [{'start': 2, 'end': 4, 'text': '这款车很好', 'source_windows': [0]},
                {'start': 2.1, 'end': 4.1, 'text': '这款车很好', 'source_windows': [1]},
                {'start': 6, 'end': 8, 'text': '这款车很好', 'source_windows': [1]}]
        merged, removed = merge_segments(rows)
        self.assertEqual(len(merged), 2)
        self.assertEqual(len(removed), 1)
        self.assertEqual(merged[-1]['start'], 6)

    def test_resume_does_not_load_model_or_overwrite_completed_output(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            (root/'windows').mkdir()
            (root/'raw').mkdir()
            (root/'model').mkdir()
            media = root/'windows/window-0000.wav'
            media.touch()
            output = root/'raw/whisper-0000.json'
            atomic_json(output, {'engine': 'whisper', 'input': str(media.resolve()),
                                 'model': str((root/'model').resolve()), 'inference_executed': True,
                                 'audio_duration': 8})
            before = output.read_bytes()
            atomic_json(root/'run.local.json', {'models': {'whisper': {'path': str(root/'model')}},
                                               'windows': [{'index': 0, 'start': 0, 'end': 8}]})
            with patch('transcribe.process') as infer:
                worker(root, 'whisper')
                infer.assert_not_called()
            self.assertEqual(output.read_bytes(), before)

    def test_offline_missing_runtime_never_starts_install(self):
        with tempfile.TemporaryDirectory() as t:
            with patch.object(sys, 'argv', ['setup_runtime.py', '--workspace', t, '--offline']), \
                 patch('setup_runtime.candidates', return_value=[]), \
                 patch('setup_runtime.shutil.which', return_value='/tool'), \
                 patch('setup_runtime.run_logged') as run:
                self.assertEqual(setup_runtime.main(), 1)
                run.assert_not_called()
            self.assertFalse((Path(t)/'.cantonese-subtitles/runtime').exists())

    def test_missing_manifest_invalidates_old_model_success(self):
        with tempfile.TemporaryDirectory() as t:
            report = Path(t)/'.cantonese-subtitles/models-check.local.json'
            atomic_json(report, {'model_load_verified': True, 'sample_inference_verified': True})
            script = Path(__file__).resolve().parents[1]/'scripts/verify_models.py'
            result = subprocess.run([sys.executable, str(script), '--workspace', t,
                                     '--manifest', str(Path(t)/'missing.json')], capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            updated = json.loads(report.read_text())
            self.assertFalse(updated['model_load_verified'])
            self.assertFalse(updated['sample_inference_verified'])


if __name__ == '__main__':
    unittest.main()
