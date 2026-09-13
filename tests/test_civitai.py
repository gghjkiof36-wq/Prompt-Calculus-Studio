import io,json,tempfile,threading,unittest
from pathlib import Path
from unittest.mock import patch

from prompt_studio.civitai import (CivitAIClient,CivitAIError,download_version,identify_models,
    normalize_version,safe_filename,sha256_file)
from prompt_studio.core import Storage
from prompt_studio.media import Catalog
from prompt_studio.credentials import save_token,load_token,clear_token,token_path


HASH='A'*64

def version(digest=HASH):
    return dict(id=22,modelId=11,name='v2',baseModel='Illustrious',trainedWords=['hero','blue eyes','hero'],
        stats={'downloadCount':12},model={'name':'Hero','type':'LORA'},files=[dict(id=33,name='hero.safetensors',
        primary=True,sizeKB=12,hashes={'SHA256':digest},downloadUrl='https://civitai.com/api/download/models/22')],
        images=[dict(url='https://image.civitai.com/a.jpeg',width=512,height=768)])

def parent():
    return dict(id=11,name='Hero Model',creator={'username':'maker'},tags=['character','anime'],stats={'downloadCount':99})


class Response(io.BytesIO):
    def __init__(self,body,headers=None): super().__init__(body); self.headers=headers or {}
    def __enter__(self): return self
    def __exit__(self,*_): self.close()


class CivitAITests(unittest.TestCase):
    def test_normalize_keeps_stable_mapping_and_unique_triggers(self):
        result=normalize_version(version(),HASH,parent())
        self.assertEqual((result['model_id'],result['version_id'],result['file_id']),(11,22,33))
        self.assertEqual(result['trained_words'],['hero','blue eyes'])
        self.assertEqual(result['base_model'],'Illustrious')
        self.assertEqual((result['creator'],result['tags'],result['model_stats']['downloadCount']),('maker',['character','anime'],99))

    def test_client_uses_official_query_names_and_bearer_header(self):
        seen=[]
        def open_(request,timeout):
            seen.append(request); return Response(json.dumps({'items':[],'metadata':{}}).encode())
        with patch('prompt_studio.civitai.urlopen',open_):
            CivitAIClient('secret').search_models('hero',types=('LORA',),base_models=('Illustrious',),limit=5)
        request=seen[0]
        self.assertIn('types=LORA',request.full_url); self.assertIn('baseModels=Illustrious',request.full_url)
        self.assertEqual(request.get_header('Authorization'),'Bearer secret')

    def test_public_hash_lookup_never_sends_token(self):
        seen=[]
        def open_(request,timeout): seen.append(request); return Response(json.dumps(version()).encode())
        with patch('prompt_studio.civitai.urlopen',open_): CivitAIClient('secret').version_by_hash(HASH)
        self.assertIsNone(seen[0].get_header('Authorization'))

    def test_hash_rejects_changed_scan_and_cancel(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'a.safetensors'; path.write_bytes(b'abc')
            with self.assertRaisesRegex(ValueError,'重新掃描'): sha256_file(path,expected={'size':4,'mtime':path.stat().st_mtime_ns})
            cancel=threading.Event(); cancel.set()
            with self.assertRaisesRegex(ValueError,'已取消'): sha256_file(path,cancel)

    def test_identification_enriches_without_replacing_user_notes(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'a.safetensors'; path.write_bytes(b'abc')
            import hashlib
            digest=hashlib.sha256(b'abc').hexdigest().upper()
            class Client:
                def versions_by_hash(self,hashes,cancel=None): self.hashes=hashes; return [version(digest)]
                def models_by_ids(self,ids,cancel=None): return [parent()]
            row=dict(id='x',path=str(path),root=directory,relative='a.safetensors',kind='LoRA',size=3,mtime=path.stat().st_mtime_ns,notes='mine')
            result=identify_models([row],Client())[0]
            self.assertEqual(result['notes'],'mine'); self.assertNotIn('trigger',result)
            self.assertEqual(result['civitai']['trained_words'],['hero','blue eyes'])
            self.assertEqual(result['civitai_status'],'matched')

    def test_catalog_source_survives_path_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            store=Storage(directory); catalog=Catalog(store); source=normalize_version(version(),HASH)
            catalog.put_source(HASH,source)
            self.assertEqual(catalog.source_for_hash(HASH)['version_id'],22)
            self.assertEqual(catalog.source_for_version(22)['model_id'],11)
            store.close()

    def test_safe_filename_rejects_traversal_and_unknown_type(self):
        self.assertEqual(safe_filename('folder/hero.safetensors'),'hero.safetensors')
        with self.assertRaises(ValueError): safe_filename('hero.exe')

    def test_download_is_atomic_verified_and_accepts_nested_search_version(self):
        payload=b'model bytes'; import hashlib
        digest=hashlib.sha256(payload).hexdigest().upper(); nested=version(digest); nested.pop('modelId'); seen=[]
        nested['files'][0]['sizeKB']=len(payload)/1024
        def open_(request,timeout): seen.append(request); return Response(payload,{'Content-Length':str(len(payload))})
        with tempfile.TemporaryDirectory() as directory,patch('prompt_studio.civitai._open_download',open_):
            result=download_version(nested,directory,'secret',parent=parent())
            self.assertEqual(Path(result['path']).read_bytes(),payload)
            self.assertEqual(result['source']['model_id'],11); self.assertEqual(seen[0].get_header('Authorization'),'Bearer secret')
            self.assertFalse(list(Path(directory).glob('*.partial')))

    def test_token_is_user_protected_and_separate_from_database(self):
        with tempfile.TemporaryDirectory() as directory:
            save_token(directory,'very-secret-token')
            raw=token_path(directory).read_bytes()
            self.assertNotIn(b'very-secret-token',raw); self.assertEqual(load_token(directory),'very-secret-token')
            clear_token(directory); self.assertEqual(load_token(directory),'')

    def test_http_errors_are_actionable(self):
        def denied(*_,**__): raise __import__('urllib.error').error.HTTPError('u',401,'',{},None)
        with patch('prompt_studio.civitai.urlopen',denied):
            with self.assertRaisesRegex(CivitAIError,'Token'): CivitAIClient('bad').test_connection()


if __name__=='__main__': unittest.main()
