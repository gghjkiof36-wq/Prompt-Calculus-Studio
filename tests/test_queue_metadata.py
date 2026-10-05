import copy
import tempfile
import unittest
from pathlib import Path
from test_comfy_integration import png
from prompt_calculus_studio.core import initial_state
from prompt_calculus_studio.pnginfo import png_metadata
from prompt_calculus_studio.snapshots import make_snapshot,image_snapshots
from prompt_calculus_studio.snapshot_assets import missing_images


class QueueMetadataTests(unittest.TestCase):
    def test_corrupt_crc_does_not_turn_valid_json_into_trusted_snapshot(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'fixture.png'
            snapshot=make_snapshot(initial_state())
            png(path,dict(prompt_studio=dict(schema_version=1,bindings=[dict(snapshot=snapshot)])))
            raw=bytearray(path.read_bytes());pos=raw.index(b'prompt_studio')
            raw[pos]=ord('P');path.write_bytes(raw)
            meta=png_metadata(path)
            self.assertEqual(image_snapshots(meta),[])
            self.assertTrue(meta['warnings'])

    def test_missing_assets_report_without_mutating_source_or_guessing_paths(self):
        with tempfile.TemporaryDirectory() as folder:
            snapshot=make_snapshot(initial_state())
            snapshot['state']['generation']=dict(source=dict(name='lost.png',relative='../lost.png',sha256='0'*64))
            snapshot['state']['multi_output']=dict(canvases={'image':dict(image=dict(layers=[dict(type='image',
                source=dict(name='layer.png',relative='missing-layer.png',sha256='1'*64))]))})
            original=copy.deepcopy(snapshot)
            self.assertEqual(missing_images(snapshot,folder),['lost.png','layer.png'])
            self.assertEqual(snapshot,original)

    def test_unknown_snapshot_version_is_not_restored(self):
        snapshot=make_snapshot(initial_state());snapshot['schema_version']=999
        meta=dict(raw=dict(prompt_studio=dict(schema_version=1,bindings=[dict(snapshot=snapshot)])))
        self.assertEqual(image_snapshots(meta),[])
