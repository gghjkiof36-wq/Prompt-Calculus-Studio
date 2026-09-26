import copy
import json
import os
import struct
import sys
import tempfile
import threading
import unittest
import zlib
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'vendor'),str(ROOT)]
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QImage,QColor,QColorSpace,QImageWriter
from prompt_studio import clean_metadata as metadata,clean_export as export
from prompt_studio.core import Storage
APP=QApplication.instance() or QApplication([])

def pngchunk(kind,data): return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data)&0xffffffff)
def exif(orientation=1): return b'II'+struct.pack('<HIH',42,8,1)+struct.pack('<HHI',0x112,3,1)+struct.pack('<H',orientation)+b'\0'*6
def jpegchunk(kind,data): return b'\xff'+bytes([kind])+struct.pack('>H',len(data)+2)+data

class CleanExportTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=ROOT/'qa'); self.root=Path(self.tmp.name); self.source=self.root/'masters'; self.source.mkdir(); self.output=self.root/'share'; self.output.mkdir()

    def tearDown(self): self.tmp.cleanup()

    def picture(self,name='source.png',icc=False):
        path=self.source/name; path.parent.mkdir(parents=True,exist_ok=True)
        image=QImage(20,12,QImage.Format.Format_ARGB32); image.fill(QColor(160,60,100,128))
        image.setPixelColor(0,0,QColor('red')); image.setPixelColor(19,11,QColor('blue'))
        if icc: image.setColorSpace(QColorSpace(QColorSpace.NamedColorSpace.SRgb))
        self.assertTrue(image.save(str(path)))
        return path

    def tagged_png(self,name='source.png'):
        path=self.picture(name); raw=path.read_bytes()
        tags=pngchunk(b'tEXt',b'prompt\0{"1":{"inputs":{"text":"red eyes"}}}')+pngchunk(b'zTXt',b'workflow\0\0'+zlib.compress(b'{"nodes":[]}'))
        tags+=pngchunk(b'iTXt',b'parameters\0\0\0\0\0secret')+pngchunk(b'eXIf',exif())+pngchunk(b'vpAg',b'private extension')
        path.write_bytes(raw[:-12]+tags+raw[-12:]+b'private trailer'); return path

    def run_export(self,paths,**values):
        rows=export.scan(paths,True); plan=export.prepare(rows,dict(export.DEFAULTS,**values),str(self.output)); result=export.execute(plan)
        self.assertTrue(all(r['status']=='Passed' for r in result['results']),result)
        return result

    def test_png_metadata_only_preserves_pixels_and_compressed_data_and_master(self):
        source=self.tagged_png(); before=source.read_bytes(); details=metadata.inspect(source)
        self.assertEqual(details['text']['workflow'],{'nodes':[]}); self.assertIn('prompt',details['text'])
        result=self.run_export([source],sha256=True); target=Path(result['results'][0]['target'])
        self.assertEqual(source.read_bytes(),before); self.assertEqual(metadata.verify(target,'all'),[])
        self.assertEqual(export.decode(source),export.decode(target)); self.assertEqual(export.sha256(source),result['results'][0]['source_sha256'])
        original=[before[p.start:p.start+p.size] for p in metadata.parts(source) if p.kind==b'IDAT']
        clean=target.read_bytes(); self.assertEqual(original,[clean[p.start:p.start+p.size] for p in metadata.parts(target) if p.kind==b'IDAT'])

    def test_generation_mode_preserves_colour_and_resolution_but_removes_text_carriers(self):
        source=self.tagged_png(); data=source.read_bytes(); end=data.rfind(b'IEND')-4
        source.write_bytes(data[:end]+pngchunk(b'pHYs',struct.pack('>IIB',3780,3780,1))+data[end:])
        result=self.run_export([source],mode='generation'); target=Path(result['results'][0]['target'])
        self.assertIn('pHYs',metadata.verify(target,'generation')); self.assertEqual(metadata.inspect(target)['text'],{})
        with self.assertRaises(ValueError): metadata.verify(target,'all')

    def test_jpeg_metadata_removed_without_recompression(self):
        source=self.picture('source.jpg'); raw=source.read_bytes()
        source.write_bytes(raw[:2]+jpegchunk(0xe1,b'Exif\0\0'+exif())+jpegchunk(0xe1,b'http://ns.adobe.com/xap/1.0/\0prompt=secret')+jpegchunk(0xed,b'Photoshop metadata')+jpegchunk(0xfe,b'parameters')+raw[2:]+b'trailer')
        before=source.read_bytes(); result=self.run_export([source]); target=Path(result['results'][0]['target']); clean=target.read_bytes()
        self.assertEqual([before[p.start:p.start+p.size] for p in metadata.parts(source) if p.kind==b'SCAN'],[clean[p.start:p.start+p.size] for p in metadata.parts(target) if p.kind==b'SCAN'])
        self.assertEqual(metadata.inspect(target)['metadata'],[])

    def test_progressive_jpeg_removes_metadata_between_scans(self):
        source=self.picture('progressive.jpg'); writer=QImageWriter(str(source),'jpeg'.encode()); writer.setProgressiveScanWrite(True)
        image=QImage(32,16,QImage.Format.Format_RGB32); image.fill(QColor('red')); self.assertTrue(writer.write(image))
        raw=source.read_bytes(); records=list(metadata.parts(source)); scans=[p for p in records if p.kind==b'SCAN']; self.assertGreater(len(scans),1)
        end=scans[0].start+scans[0].size; source.write_bytes(raw[:end]+jpegchunk(0xfe,b'private prompt between scans')+raw[end:])
        result=self.run_export([source]); target=Path(result['results'][0]['target'])
        self.assertNotIn('Comment',metadata.inspect(target)['metadata']); self.assertEqual(export.decode(source),export.decode(target))

    def test_apng_frames_preserved_and_conversion_rejected(self):
        source=self.picture(); raw=source.read_bytes(); records=list(metadata.parts(source))
        idat=b''.join(raw[p.payload:p.payload+p.length] for p in records if p.kind==b'IDAT')
        frame=lambda seq:struct.pack('>IIIIIHHBB',seq,20,12,0,0,1,10,0,0)
        source.write_bytes(raw[:33]+pngchunk(b'acTL',struct.pack('>II',2,0))+pngchunk(b'fcTL',frame(0))+raw[33:-12]+pngchunk(b'fcTL',frame(1))+pngchunk(b'fdAT',struct.pack('>I',2)+idat)+pngchunk(b'tEXt',b'prompt\0secret')+raw[-12:])
        result=self.run_export([source]); target=Path(result['results'][0]['target']); self.assertTrue(metadata.inspect(target)['animated'])
        self.assertEqual(sum(p.kind==b'fcTL' for p in metadata.parts(target)),2)
        plan=export.prepare(export.scan([source]),dict(format='jpeg'),str(self.output)); self.assertEqual(plan['entries'][0]['action'],'fail')

    def test_webp_metadata_and_flags_removed(self):
        source=self.picture('source.webp'); raw=source.read_bytes()
        # Qt may already emit VP8X for alpha; inject metadata and set its flags.
        records=list(metadata.parts(source)); body=bytearray(raw[12:])
        if records[0].kind==b'VP8X': body[8]|=0x0c
        else: body=bytearray(b'VP8X'+struct.pack('<I',10)+b'\x0c\0\0\0'+(19).to_bytes(3,'little')+(11).to_bytes(3,'little'))+body
        for kind,data in [(b'EXIF',exif()),(b'XMP ',b'prompt=secret'),(b'PRIV',b'private')]: body+=kind+struct.pack('<I',len(data))+data+(b'\0' if len(data)&1 else b'')
        source.write_bytes(b'RIFF'+struct.pack('<I',len(body)+4)+b'WEBP'+body+b'trailer')
        result=self.run_export([source]); target=Path(result['results'][0]['target']); self.assertEqual(metadata.verify(target,'all'),[])
        vp8x=next((p for p in metadata.parts(target) if p.kind==b'VP8X'),None)
        if vp8x: self.assertEqual(vp8x.prefix[0]&0x2c,0)

    def test_conversions_resize_quality_and_bmp(self):
        source=self.tagged_png()
        for fmt in ('png','jpeg','webp'):
            with self.subTest(fmt=fmt):
                result=self.run_export([source],format=fmt,resize='exact',width=10,height=10,aspect=True,quality=75)
                target=Path(result['results'][0]['target']); self.assertEqual(metadata.format_of(target),fmt)
                self.assertEqual((export.decode(target).width(),export.decode(target).height()),(10,6)); metadata.verify(target,'all')
        bmp=self.picture('source.bmp'); result=self.run_export([bmp]); metadata.verify(result['results'][0]['target'],'all')

    def test_icc_and_orientation_are_baked_before_removal(self):
        source=self.picture(icc=True); result=self.run_export([source]); self.assertFalse(metadata.inspect(result['results'][0]['target'])['icc'])
        jpg=self.picture('rotate.jpg'); raw=jpg.read_bytes(); jpg.write_bytes(raw[:2]+jpegchunk(0xe1,b'Exif\0\0'+exif(6))+raw[2:])
        result=self.run_export([jpg]); target=export.decode(result['results'][0]['target']); self.assertEqual((target.width(),target.height()),(12,20))
        self.assertEqual(metadata.inspect(result['results'][0]['target'])['orientation'],1)

    def test_recursive_structure_names_and_invalid_rules(self):
        a=self.picture('a.png'); b=self.picture('folder/b.png')
        self.assertEqual(len(export.scan([self.source])),1)
        result=self.run_export([self.source],pattern='{index:04d}-{name}')
        self.assertEqual([Path(r['target']).relative_to(self.output).as_posix() for r in result['results']],['0001-a.png','folder/0002-b.png'])
        for pattern in ('../{name}','{name.__class__}','{bad}','{index:1000000d}','CON','x.'):
            with self.assertRaises((ValueError,KeyError)): export.options(dict(pattern=pattern))

    def test_skip_overwrite_rename_and_no_duplicate_internal_target(self):
        source=self.picture(); first=self.run_export([source]); target=Path(first['results'][0]['target']); before=target.read_bytes()
        rows=export.scan([source]); plan=export.prepare(rows,dict(collision='skip'),str(self.output)); self.assertEqual(export.execute(plan)['results'][0]['status'],'Skipped')
        target.write_bytes(b'old export'); self.run_export([source],collision='overwrite'); self.assertEqual(target.read_bytes(),before)
        result=self.run_export([source]); self.assertEqual(Path(result['results'][0]['target']).name,'source (1).png')
        other=self.picture('sub/source.png'); plan=export.prepare(export.scan([source,other]),dict(collision='overwrite',structure=False),str(self.output))
        self.assertEqual(plan['entries'][1]['action'],'fail')

    def test_all_masters_hardlinks_appdata_and_changed_target_protected(self):
        source=self.picture(); rows=export.scan([source]); before=source.read_bytes()
        for collision in ('skip','overwrite'):
            plan=export.prepare(rows,dict(collision=collision),str(self.source)); self.assertEqual(plan['entries'][0]['action'],'fail')
        hard=self.output/source.name; os.link(source,hard)
        plan=export.prepare(rows,dict(collision='overwrite'),str(self.output)); self.assertEqual(plan['entries'][0]['action'],'fail'); hard.unlink()
        plan=export.prepare(rows,{},str(self.output),forbidden=[self.output]); self.assertEqual(plan['entries'][0]['action'],'fail')
        plan=export.prepare(rows,{},str(self.output)); (self.output/source.name).write_bytes(b'another export appeared')
        self.assertEqual(export.execute(plan)['results'][0]['status'],'Failed'); self.assertEqual(source.read_bytes(),before)
        plan=export.prepare(rows,dict(collision='overwrite'),str(self.output)); source.write_bytes(before+b'changed')
        self.assertEqual(export.execute(plan)['results'][0]['status'],'Failed')

    def test_failure_and_cancel_leave_no_temp_and_audit_never_catalogues_images(self):
        source=self.picture(); bad=self.source/'bad.png'; bad.write_bytes(b'not image')
        plan=export.prepare(export.scan([source,bad]),{},str(self.output)); result=export.execute(plan)
        self.assertEqual([r['status'] for r in result['results']],['Passed','Failed'])
        self.assertEqual(list(self.output.glob('.prompt-studio*')),[])
        cancel=threading.Event(); cancel.set(); cancelled=export.execute(plan,cancel); self.assertEqual(cancelled['results'][0]['status'],'Cancelled')
        store=Storage(self.root/'database'); records=export.ExportRecords(store); records.save_run(result)
        records.save_preset('My Clean',dict(quality=81)); self.assertEqual(records.presets()['My Clean']['quality'],81)
        self.assertEqual(records.history()[0]['results'],result['results']); self.assertEqual(list((self.root/'database').glob('*.png')),[]); store.close()

    def test_corrupt_png_and_unexpected_metadata_fail_verification(self):
        source=self.tagged_png()
        with self.assertRaises(ValueError): metadata.verify(source,'all')
        raw=bytearray(source.read_bytes()); raw[45]^=1; source.write_bytes(raw)
        self.assertTrue(export.scan([source])[0]['error'])

    def test_inspector_expands_large_workflow_and_labels_truncated_content(self):
        source=self.picture(); raw=source.read_bytes(); workflow=json.dumps({'note':'x'*100000}).encode()
        source.write_bytes(raw[:-12]+pngchunk(b'zTXt',b'workflow\0\0'+zlib.compress(workflow))+raw[-12:])
        self.assertEqual(len(metadata.inspect(source)['text']['workflow']['note']),100000)
        source.write_bytes(raw[:-12]+pngchunk(b'zTXt',b'workflow\0\0'+zlib.compress(b'x'*(metadata.TEXT_LIMIT+1)))+raw[-12:])
        self.assertIn('僅顯示前段',metadata.inspect(source)['text']['workflow'])

if __name__=='__main__': unittest.main()
