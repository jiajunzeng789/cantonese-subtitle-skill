import importlib.util
from pathlib import Path
import tempfile
import unittest

script=Path(__file__).resolve().parents[1]/'install.py'
spec=importlib.util.spec_from_file_location('macos_installer',script)
installer=importlib.util.module_from_spec(spec);spec.loader.exec_module(installer)


class MacOSInstallerTests(unittest.TestCase):
    def test_reinstall_keeps_python_symlinks_and_unrelated_files(self):
        with tempfile.TemporaryDirectory() as t:
            source=Path(t)/'source';target=Path(t)/'target'
            (source/'bin').mkdir(parents=True)
            (source/'bin/python3.13').write_text('runtime')
            (source/'bin/python3').symlink_to('python3.13')
            installer.copy_tree(source,target)
            (target/'keep.txt').write_text('unrelated')
            installer.copy_tree(source,target)
            self.assertEqual((target/'bin/python3').readlink(),Path('python3.13'))
            self.assertEqual((target/'bin/python3').read_text(),'runtime')
            self.assertEqual((target/'keep.txt').read_text(),'unrelated')

    def test_copy_never_follows_an_unexpected_destination_link(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);source=root/'source';target=root/'target';outside=root/'outside'
            source.mkdir();target.mkdir();outside.write_text('private')
            (source/'file').write_text('new')
            (target/'file').symlink_to(outside)
            with self.assertRaises(RuntimeError):installer.copy_tree(source,target)
            self.assertEqual(outside.read_text(),'private')


if __name__=='__main__':unittest.main()
