"""0.9 release must cover every pin and each newly bundled native provider."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
import zipfile
from types import SimpleNamespace
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path);loaded=importlib.util.module_from_spec(spec);spec.loader.exec_module(loaded);return loaded
notices=module('v09_collector',ROOT/'packaging/collect_notices.py')
runtime=module('v09_runtime',ROOT/'packaging/runtime_wheels.py')
dependencies=module('v09_dependencies',ROOT/'packaging/release_dependencies.py')
builder=module('v09_lxml_builder',ROOT/'packaging/build_lxml_windows.py')


class V09NoticesTests(unittest.TestCase):
    def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
    def tearDown(self):self.tmp.cleanup()

    def test_every_runtime_pin_is_required_and_test_only_reportlab_is_removed(self):
        packages=dependencies.release_distributions();pins=notices.release_pins()
        normalized={notices.normalized_name(name):version for name,version in packages.items()}
        self.assertEqual(normalized,{name:version for name,version in pins.items() if name!='reportlab'})
        self.assertTrue({'PyMuPDF','pyHanko','pyhanko-certvalidator','lxml','aiohttp','tzdata'}<=packages.keys())

    def test_sources_cover_all_python_dependencies_including_pure_signing_packages(self):
        packages=dependencies.release_distributions();queried=[]
        def fetch(url):
            name=url.split('/')[-3];queried.append(name)
            return json.dumps({'urls':[{'packagetype':'sdist','filename':name+'.tar.gz','url':'https://example.invalid/'+name+'.tar.gz','digests':{'sha256':'a'*64},'size':100}]}).encode()
        with patch.object(notices,'fetch',side_effect=fetch):
            result=notices.source_archives({**packages,'Python':'3.14.8'},{'qt_source_archive':{'component':'Qt'}})
        self.assertEqual(set(queried),set(packages)-{'PyQt6-Qt6'})
        self.assertEqual(len(result),len(packages)+1)

    def test_complete_original_mupdf_and_windows_lxml_corpus_is_locked_offline(self):
        lock=notices.copy_v09_locked_notices(self.root)
        self.assertEqual(len(lock['mupdf_submodules']),18)
        self.assertGreaterEqual(len(lock['files']),125)
        self.assertGreater((self.root/'upstream/v09/pymupdf-1.28.2/COPYING').stat().st_size,30000)
        self.assertEqual(lock['windows_lxml']['libxml_compiled'],[2,11,9])
        self.assertEqual({entry['name'] for entry in lock['windows_lxml']['submodules']},{'libiconv','libxml2','libxslt','zlib'})
        for item in lock['files']:self.assertEqual(hashlib.sha256((self.root/Path(item['path']).relative_to('licenses')).read_bytes()).hexdigest(),item['sha256'])

    def test_missing_native_notice_component_or_full_agpl_fails_closed(self):
        lock=json.loads(notices.V09_LOCK.read_text());lock['files']=[x for x in lock['files'] if '/thirdparty/freetype/' not in x['path']]
        altered=self.root/'lock.json';altered.write_text(json.dumps(lock))
        with patch.object(notices,'V09_LOCK',altered),self.assertRaisesRegex(notices.IncompleteNotices,'freetype'):
            notices.copy_v09_locked_notices(self.root/'output')

    def test_exact_new_native_identities_and_unknown_siblings(self):
        allowed={'PyMuPDF':['pymupdf/_extra.so','pymupdf/_mupdf.pyd','pymupdf/libmupdf.so.28.2','pymupdf/mupdfcpp64.dll'],
                 'aiohttp':['aiohttp/_http_parser.cp314-win_amd64.pyd','aiohttp/_websocket/reader_c.cpython-314-x86_64-linux-gnu.so'],
                 'lxml':['lxml/etree.cp314-win_amd64.pyd','lxml/html/diff.cpython-314-x86_64-linux-gnu.so'],
                 'yarl':['yarl/_quoting_c.cpython-314-x86_64-linux-gnu.so']}
        for name,paths in allowed.items():
            for path in paths:self.assertTrue(runtime.native_identity(name,path),(name,path))
        for name,path in [('PyMuPDF','pymupdf/libmupdf.so.29.0'),('PyMuPDF','pymupdf/unknown.dll'),('lxml','lxml/libxml2.dll'),('aiohttp','aiohttp/_injected.so')]:self.assertFalse(runtime.native_identity(name,path))

    def test_mupdf_native_provider_requires_both_matching_sdists_and_record_hash(self):
        path=self.root/'libmupdf.so.28.2';path.write_bytes(b'recorded binary')
        record={'name':'PyMuPDF','version':'1.28.2','native_files':[{'path':str(path.resolve()),'relative_path':'pymupdf/libmupdf.so.28.2','sha256':hashlib.sha256(path.read_bytes()).hexdigest()}]}
        inventory={'schema_version':1,'platform':'linux','distributions':[record],'source_archives':[{'component':'PyMuPDF','version':'1.28.2'}]}
        with self.assertRaisesRegex(runtime.RuntimeAuditError,'complete matching'):runtime.native_provider(path,inventory)
        inventory['source_archives'].append({'component':'MuPDF','version':'1.28.2'})
        self.assertEqual(runtime.native_provider(path,inventory)['distribution'],'PyMuPDF')
        path.write_bytes(b'mutated')
        with self.assertRaisesRegex(runtime.RuntimeAuditError,'changed since'):runtime.native_provider(path,inventory)

    def test_frozen_new_runtime_files_bind_to_their_record_and_reject_unknown(self):
        bundle=self.root/'bundle';path=bundle/'_internal/lxml/etree.cp314-win_amd64.pyd';path.parent.mkdir(parents=True);path.write_bytes(b'lxml native')
        files=[{'relative_path':stem+'.cp314-win_amd64.pyd','sha256':hashlib.sha256(path.read_bytes()).hexdigest()} for stem in runtime.NATIVE_MODULES['lxml']]
        receipt=runtime.windows_lxml_receipt_identity();receipt.update(native_files={item['relative_path']:item['sha256'] for item in files},build_inputs_verified=True)
        inventory={'schema_version':1,'platform':'win32','source_archives':runtime.v09_lock()['windows_lxml']['source_archives']+[{'component':'lxml','version':'6.1.3'}],'distributions':[{'name':'lxml','version':'6.1.3','native_files':files,'native_build_receipt':receipt}]}
        self.assertEqual(runtime.verify_frozen_runtime(bundle,inventory)[0]['distribution'],'lxml')
        with patch.dict(inventory,source_archives=[]),self.assertRaisesRegex(runtime.RuntimeAuditError,'corresponding source'):
            runtime.verify_frozen_runtime(bundle,inventory)
        with patch.dict(inventory['distributions'][0],native_build_receipt=None),self.assertRaisesRegex(runtime.RuntimeAuditError,'receipt'):
            runtime.verify_frozen_runtime(bundle,inventory)
        path.with_name('injected.dll').write_bytes(b'unknown')
        with self.assertRaisesRegex(runtime.RuntimeAuditError,'Unknown bundled'):runtime.verify_frozen_runtime(bundle,inventory)

    def test_controlled_lxml_patch_rejects_changed_original_and_eliminates_latest_lookup(self):
        value=builder.lock();original=(ROOT/value['build_script']['path']).read_bytes()
        result,patch_data=builder.patched_script(original,value['build_script']['sha256'])
        self.assertNotIn(b'read_latest_release',result)
        self.assertNotIn(b'api.github.com/repos/lxml/libxml2-win-binaries/releases',result)
        self.assertIn(b'pdfwb-native-inputs.json',result)
        self.assertTrue(patch_data.startswith(b'--- a/buildlibxml.py'))
        with self.assertRaisesRegex(builder.BuildError,'pinned original'):
            builder.patched_script(original+b'changed',value['build_script']['sha256'])

    def test_controlled_lxml_actual_input_acknowledgement_rejects_mutation(self):
        root=self.root/'lib';root.mkdir();(root/'static.lib').write_bytes(b'verified static library')
        script=self.root/'buildlibxml.py';marker=self.root/'used.json'
        config={'inputs':{'libxml2':{'root':str(root),'files':{'static.lib':builder.digest((root/'static.lib').read_bytes())}}},'used_marker':str(marker)}
        script.with_name('pdfwb-native-inputs.json').write_text(json.dumps(config))
        scope={'sys':__import__('sys'),'__file__':str(script)};exec(builder.REPLACEMENT,scope)
        self.assertEqual(scope['download_and_extract_windows_binaries']('unused'),{'libxml2':str(root)})
        self.assertEqual(json.loads(marker.read_text()),config)
        marker.unlink();(root/'static.lib').write_bytes(b'changed library')
        with self.assertRaisesRegex(RuntimeError,'input changed'):scope['download_and_extract_windows_binaries']('unused')
        self.assertFalse(marker.exists())

    def test_controlled_lxml_receipt_binds_assets_patch_helper_and_output(self):
        record={'native_files':[{'relative_path':stem+'.cp314-win_amd64.pyd','sha256':'a'*64} for stem in runtime.NATIVE_MODULES['lxml']]}
        receipt=runtime.windows_lxml_receipt_identity();receipt.update(build_inputs_verified=True,native_files={item['relative_path']:item['sha256'] for item in record['native_files']})
        runtime.verify_windows_lxml_receipt(receipt,record)
        for key in ('helper_sha256','patch_sha256','native_assets','source','build_inputs_verified','native_files'):
            with self.subTest(key=key),patch.dict(receipt,{key:None}),self.assertRaisesRegex(runtime.RuntimeAuditError,'receipt'):
                runtime.verify_windows_lxml_receipt(receipt,record)

    def test_controlled_lxml_download_cache_rejects_foreign_changed_bytes(self):
        item={'filename':'native.zip','url':'https://example.invalid/native.zip','size':4,'sha256':builder.digest(b'good')}
        target=self.root/(item['sha256']+'-'+item['filename']);target.write_bytes(b'evil')
        with patch.object(builder.urllib.request,'urlopen') as network,self.assertRaisesRegex(builder.BuildError,'Cached'):
            builder.download(item,self.root)
        network.assert_not_called();self.assertEqual(target.read_bytes(),b'evil')

    def test_controlled_lxml_extract_rejects_traversal_and_links(self):
        archive=self.root/'asset.zip'
        with zipfile.ZipFile(archive,'w') as output:output.writestr('../escape',b'bad')
        with self.assertRaisesRegex(builder.BuildError,'Unsafe'):builder.extract(archive,self.root/'extract',zipped=True)
        self.assertFalse((self.root/'escape').exists())
        archive=self.root/'link.tar.gz'
        with tarfile.open(archive,'w:gz') as output:
            member=tarfile.TarInfo('root/link');member.type=tarfile.SYMTYPE;member.linkname='/foreign';output.addfile(member)
        with self.assertRaisesRegex(builder.BuildError,'Linked/special'):builder.extract(archive,self.root/'extract2')

    def test_controlled_lxml_receipt_is_authenticated_by_installed_record(self):
        import base64,csv
        record=self.root/'lxml-6.1.3.dist-info/RECORD';record.parent.mkdir()
        record.write_text('lxml-6.1.3.dist-info/RECORD,,\n')
        entry=Path('lxml-6.1.3.dist-info/RECORD')
        dist=SimpleNamespace(files=[entry],locate_file=lambda relative:self.root/str(relative))
        receipt=builder.attach_receipt(dist,{'build_inputs_verified':True})
        rows=list(csv.reader(record.read_text().splitlines()));row=rows[-1]
        digest_value=row[1].removeprefix('sha256=')
        self.assertEqual(digest_value,base64.urlsafe_b64encode(hashlib.sha256(receipt.read_bytes()).digest()).decode().rstrip('='))
        class ReceiptEntry:
            hash=SimpleNamespace(mode='sha256',value=digest_value)
            size=int(row[2])
            def __str__(self):return row[0]
        runtime._verified_file(dist,ReceiptEntry())
        receipt.write_bytes(b'changed receipt')
        with self.assertRaisesRegex(runtime.RuntimeAuditError,'digest/size mismatch'):runtime._verified_file(dist,ReceiptEntry())

    def archive(self,files):
        path=self.root/'source.tar.gz'
        with tarfile.open(path,'w:gz') as archive:
            for name,data in files.items():
                item=tarfile.TarInfo('source-root/'+name);item.size=len(data);archive.addfile(item,io.BytesIO(data))
        return path

    def test_complete_corresponding_source_members_not_empty_git_submodules(self):
        item={'filename':'mupdf-source.tar.gz','required_member_suffixes':['COPYING','source/fitz/document.c','thirdparty/freetype/src/base/ftbase.c']}
        archive=self.archive({'COPYING':b'AGPL','source/fitz/document.c':b'C source'})
        with self.assertRaisesRegex(notices.IncompleteNotices,'incomplete'):notices.verify_source_members(archive,item)
        archive=self.archive({'COPYING':b'AGPL','source/fitz/document.c':b'C source','thirdparty/freetype/src/base/ftbase.c':b'real nested source'})
        notices.verify_source_members(archive,item)


if __name__=='__main__':unittest.main()
