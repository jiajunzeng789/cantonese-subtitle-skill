"""Display continuity must preserve source starts and reject invalid alignment."""
import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from export_srt import prepare_display


class DisplayTests(unittest.TestCase):
    def data(self):
        return {'media_duration': 30, 'subtitle_profile': {'layout': 'landscape'}, 'cues': [
            {'start': 0, 'end': 1, 'text': '第一句', 'speech_end': .9},
            {'start': 2, 'end': 3, 'text': '第二句'},
            {'start': 7, 'end': 8, 'text': '第三句'},
            {'start': 9, 'end': 15, 'text': '第四句'},
            {'start': 17, 'end': 18, 'text': '最后一句'}]}

    def test_continuity_without_mutating_source_or_crossing_long_pause(self):
        original = self.data()
        before = copy.deepcopy(original)
        prepared, report = prepare_display(original)
        self.assertEqual(original, before)
        self.assertEqual([c['start'] for c in prepared['cues']], [c['start'] for c in original['cues']])
        self.assertEqual([c['text'] for c in prepared['cues']], [c['text'] for c in original['cues']])
        self.assertEqual([c['end'] for c in prepared['cues']], [2, 3, 9, 15, 18])
        self.assertEqual(prepared['cues'][0]['speech_end'], .9)
        self.assertEqual(report['changed_count'], 2)
        self.assertTrue(all(a['end'] <= b['start'] for a, b in zip(prepared['cues'], prepared['cues'][1:])))

    def test_explicit_cut_and_original_mode_preserve_ends(self):
        source = self.data()
        source['cues'][0]['hold_to_next'] = False
        prepared, _ = prepare_display(source)
        self.assertEqual(prepared['cues'][0]['end'], 1)
        source['display_policy'] = {'mode': 'original'}
        prepared, report = prepare_display(source)
        self.assertEqual(prepared['cues'], source['cues'])
        self.assertEqual(report['changed_count'], 0)

    def test_invalid_alignment_is_not_hidden_by_extension(self):
        for start, end in [(0, 0), (0, 2.5), (-1, 1), (0, float('nan')), (0, 31)]:
            source = self.data()
            source['cues'][0].update(start=start, end=end)
            with self.subTest(start=start, end=end), self.assertRaises(ValueError):
                prepare_display(source)

    def test_repeated_preparation_does_not_extend_further(self):
        once, _ = prepare_display(self.data())
        twice, report = prepare_display(once)
        self.assertEqual(once, twice)
        self.assertEqual(report['changed_count'], 0)

    def test_invalid_policy_and_boundary_values_are_rejected(self):
        for policy in [{'mode': 'typo'}, {'max_gap_seconds': -1}, {'max_duration_seconds': float('inf')}, []]:
            source = self.data()
            source['display_policy'] = policy
            with self.subTest(policy=policy), self.assertRaises(ValueError):
                prepare_display(source)
        source = self.data()
        source['cues'][0]['hold_to_next'] = 'false'
        with self.assertRaises(ValueError):
            prepare_display(source)

    def test_user_can_set_thresholds_without_changing_onsets(self):
        source = self.data()
        source['display_policy'] = {'max_gap_seconds': 4, 'max_duration_seconds': 8}
        prepared, _ = prepare_display(source)
        self.assertEqual([c['end'] for c in prepared['cues']], [2, 7, 9, 17, 18])


if __name__ == '__main__':
    unittest.main()
