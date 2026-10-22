"""Content-removal boundaries, immutable text replacement and real signatures."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import io
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from pypdf import PdfReader, PdfWriter
from pypdf.generic import NameObject, NumberObject, DecodedStreamObject
from reportlab.pdfgen.canvas import Canvas

from pdf_workbench.advanced_pdf import (RedactionRegion, existing_text_runs, export_redacted_pdf,
    mupdf, prepare_text_replacement, publish_new_pdf, workspace_pdf_bytes)
from pdf_workbench.annotations import PageAnnotation
from pdf_workbench.core import OperationCancelled, PdfWorkbenchError, Workspace, export_pdf, load_source
from pdf_workbench.pdfa import PdfaValidation,export_pdfa,parse_verapdf_report
from pdf_workbench.security_signatures import sign_pdf_certificate, verify_pdf_signatures


class AdvancedPdfTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        self.original = self.root / "original.pdf"
        canvas = Canvas(str(self.original), pagesize=(360,240), pageCompression=0)
        canvas.setAuthor("PRIVATE METADATA"); canvas.setTitle("SECRET TITLE")
        canvas.drawString(30,180,"SECRET Alpha"); canvas.drawString(30,90,"Keep Bravo"); canvas.showPage()
        canvas.drawString(30,100,"SECOND page"); canvas.save()
        self.original_hash = hashlib.sha256(self.original.read_bytes()).hexdigest()
        self.workspace = Workspace(); self.workspace.add_sources([load_source(self.original)])

    def tearDown(self):
        self.assertEqual(hashlib.sha256(self.original.read_bytes()).hexdigest(), self.original_hash)
        self.temp.cleanup()

    def region(self): return RedactionRegion(self.workspace.pages[0].uid,(.06,.15,.65,.30))

    def test_redaction_physically_drops_text_metadata_annotations_and_revisions(self):
        page = self.workspace.pages[0]
        self.workspace.add_annotation(page.uid,PageAnnotation("secret-note","comment",(.1,.1,.2,.2),"SECRET COMMENT"))
        result = export_redacted_pdf(self.workspace.sources,self.workspace.pages,self.root/"clean.pdf",[self.region()],confirmed=True)
        data = result.path.read_bytes(); reader = PdfReader(io.BytesIO(data))
        self.assertEqual(len(reader.pages),2); self.assertFalse(reader.is_encrypted)
        self.assertIsNone(reader.trailer["/Root"].get("/AcroForm")); self.assertIsNone(reader.trailer["/Root"].get("/Outlines"))
        self.assertIsNone(reader.trailer["/Root"].get("/Names")); self.assertEqual(data.count(b"%%EOF"),1)
        fitz = mupdf()
        with fitz.open(stream=data,filetype="pdf") as document:
            for p in document:
                self.assertEqual(p.get_text(),""); self.assertEqual(list(p.annots() or ()),[])
            for xref in range(1,document.xref_length()):
                raw = document.xref_object(xref).encode()+ (document.xref_stream(xref) or b"")
                for secret in (b"SECRET Alpha",b"SECRET TITLE",b"PRIVATE METADATA",b"SECRET COMMENT"):
                    self.assertNotIn(secret,raw)
            pixel = document[0].get_pixmap(matrix=fitz.Matrix(1,1),alpha=False)
            self.assertEqual(pixel.pixel(60,55),(0,0,0))
        self.assertEqual(result.regions,1); self.assertEqual(len(self.workspace.pages),2)

    def test_redaction_blackens_annotations_and_rejects_no_confirmation(self):
        page = self.workspace.pages[0]
        self.workspace.add_annotation(page.uid,PageAnnotation("sensitive","text",(.1,.18,.5,.26),"SECRET OBJECT"))
        with self.assertRaises(PdfWorkbenchError):
            export_redacted_pdf(self.workspace.sources,self.workspace.pages,self.root/"unconfirmed.pdf",[self.region()])
        result=export_redacted_pdf(self.workspace.sources,self.workspace.pages,self.root/"confirmed.pdf",[self.region()],confirmed=True)
        with mupdf().open(result.path) as document:
            pix=document[0].get_pixmap(matrix=mupdf().Matrix(1,1),alpha=False)
            self.assertEqual(pix.pixel(60,55),(0,0,0))

    def test_redaction_final_rotation_order_crop_and_user_unit(self):
        fitz=mupdf()
        for intrinsic in (0,90,180,270):
            for extra in (0,90,180,270):
                with self.subTest(intrinsic=intrinsic,extra=extra):
                    writer=PdfWriter(clone_from=self.original)
                    page=writer.pages[0];page.cropbox.lower_left=(20,20);page.cropbox.upper_right=(340,220)
                    page[NameObject('/UserUnit')]=NumberObject(2);page.rotate(intrinsic)
                    sourcepath=self.root/f'rot-source-{intrinsic}-{extra}.pdf';writer.write(sourcepath)
                    ws=Workspace();ws.add_sources([load_source(sourcepath)]);ws.rotate([ws.pages[0].uid],extra);ws.move([ws.pages[1].uid],-1)
                    before=workspace_pdf_bytes(ws.sources,ws.pages)
                    uid=ws.pages[1].uid
                    result=export_redacted_pdf(ws.sources,ws.pages,self.root/f'rot-out-{intrinsic}-{extra}.pdf',[RedactionRegion(uid,(.1,.1,.3,.3))],confirmed=True,dpi=100)
                    with fitz.open(stream=before,filetype='pdf') as original,fitz.open(result.path) as redacted:
                        for i in range(2):
                            self.assertAlmostEqual(original[i].rect.width,redacted[i].rect.width,places=3)
                            self.assertAlmostEqual(original[i].rect.height,redacted[i].rect.height,places=3)
                        pix=redacted[1].get_pixmap(matrix=fitz.Matrix(1,1),alpha=False)
                        self.assertEqual(pix.pixel(round(pix.width*.2),round(pix.height*.2)),(0,0,0))
                        untouched=original[0].get_pixmap(matrix=fitz.Matrix(1,1),alpha=False)
                        clean=redacted[0].get_pixmap(matrix=fitz.Matrix(1,1),alpha=False)
                        self.assertEqual((untouched.width,untouched.height),(clean.width,clean.height))

    def test_redaction_cancel_never_creates_destination(self):
        target=self.root/'cancelled.pdf'
        with self.assertRaises(OperationCancelled):
            export_redacted_pdf(self.workspace.sources,self.workspace.pages,target,[self.region()],confirmed=True,cancel=lambda:True)
        self.assertFalse(target.exists())

    def test_output_never_overwrites_original_existing_or_symlink(self):
        data=workspace_pdf_bytes(self.workspace.sources,self.workspace.pages)
        for target in (self.original,self.root/'existing.pdf'):
            if target!=self.original:target.write_bytes(b'preserve')
            before=target.read_bytes()
            with self.assertRaises(PdfWorkbenchError):publish_new_pdf(data,target,self.workspace.sources)
            self.assertEqual(target.read_bytes(),before)
        target=self.root/'alias.pdf'
        try:target.symlink_to(self.original)
        except OSError: return
        with self.assertRaises(PdfWorkbenchError):publish_new_pdf(data,target,self.workspace.sources)

    def test_publisher_cancellation_preserves_a_foreign_in_place_edit(self):
        writer=PdfWriter();page=writer.add_blank_page(width=200,height=200)
        stream=DecodedStreamObject();stream.set_data(b'%'+b'A'*(1500*1024)+b'\n')
        page[NameObject('/Contents')]=writer._add_object(stream)
        output=io.BytesIO();writer.write(output);data=output.getvalue();target=self.root/'fallback.pdf'
        def cancel():
            if target.exists() and target.stat().st_size>0:
                target.write_bytes(b'foreign edit');return True
            return False
        with self.assertRaises(OperationCancelled):publish_new_pdf(data,target,self.workspace.sources,cancel)
        self.assertEqual(target.read_bytes(),b'foreign edit')

    def test_publisher_cancellation_cleans_only_our_own_partial_output(self):
        data=workspace_pdf_bytes(self.workspace.sources,self.workspace.pages);target=self.root/'ownpartial.pdf'
        with self.assertRaises(OperationCancelled):
            publish_new_pdf(data,target,self.workspace.sources,cancel=lambda:target.exists())
        self.assertFalse(target.exists())

    def test_publisher_commits_checked_bytes_without_a_mutable_intermediate_file(self):
        data=workspace_pdf_bytes(self.workspace.sources,self.workspace.pages);target=self.root/'no-intermediate.pdf'
        observed=[]
        def cancel():
            observed.extend(self.root.glob('.pdf-workbench-*.tmp'));return False
        with patch('tempfile.mkstemp',side_effect=AssertionError('Mutable staging file must not be created')):
            publish_new_pdf(data,target,self.workspace.sources,cancel)
        self.assertEqual(observed,[]);self.assertEqual(target.read_bytes(),data)

    def test_text_is_removed_not_overlaid_and_other_pages_copies_unchanged_undo(self):
        ws=self.workspace;uid=ws.pages[0].uid;copy=ws.duplicate([uid])[0]
        before=tuple(ws.pages);source=ws.sources[ws.pages[0].source_id]
        runs=existing_text_runs(source,0);self.assertEqual(runs[0].text,'SECRET Alpha');self.assertTrue(runs[0].supported)
        result=prepare_text_replacement(ws.sources,ws.pages,uid,runs[0].run_id,'PUBLIC Beta')
        ws.apply_page_replacements(result.sources,result.pages)
        exported=self.root/'edited.pdf';export_pdf(ws.sources,ws.pages,exported)
        reader=PdfReader(exported);texts=[p.extract_text() for p in reader.pages]
        self.assertIn('PUBLIC Beta',texts[0]);self.assertNotIn('SECRET Alpha',texts[0]);self.assertIn('Keep Bravo',texts[0])
        copyindex=next(i for i,p in enumerate(ws.pages) if p.uid==copy);self.assertIn('SECRET Alpha',texts[copyindex])
        newsource=result.sources[0]
        with mupdf().open(stream=newsource.data,filetype='pdf') as document:
            for xref in range(1,document.xref_length()):
                self.assertNotIn(b'SECRET Alpha',document.xref_stream(xref) or b'')
        ws.undo();self.assertEqual(tuple(ws.pages),before);ws.redo();self.assertEqual(tuple(ws.pages),result.pages)

    def test_text_rejects_too_long_unicode_and_cancellation_atomically(self):
        ws=self.workspace;before=tuple(ws.pages);uid=ws.pages[0].uid
        for value in ('This replacement is much too long for the original run','漢字','line\nbreak',''):
            with self.subTest(value=value),self.assertRaises(PdfWorkbenchError):
                prepare_text_replacement(ws.sources,ws.pages,uid,'0',value)
        with self.assertRaises(OperationCancelled):
            prepare_text_replacement(ws.sources,ws.pages,uid,'0','Okay',cancel=lambda:True)
        self.assertEqual(tuple(ws.pages),before)

    def test_text_rejects_unsupported_embedded_fonts_and_overlap(self):
        path=self.root/'overlap.pdf';c=Canvas(str(path),pagesize=(360,240))
        c.drawString(30,180,'FIRST');c.drawString(31,180,'SECOND');c.save()
        ws=Workspace();ws.add_sources([load_source(path)])
        with self.assertRaises(PdfWorkbenchError):prepare_text_replacement(ws.sources,ws.pages,ws.pages[0].uid,'0','A')

    def test_text_rotations_are_preserved(self):
        ws=self.workspace;ws.rotate([ws.pages[0].uid],90)
        result=prepare_text_replacement(ws.sources,ws.pages,ws.pages[0].uid,'0','PUBLIC Beta')
        self.assertEqual(result.pages[0].rotation,90);self.assertEqual(result.pages[0].uid,ws.pages[0].uid)

    def test_text_rejects_overlapping_native_links_without_dropping_actions(self):
        path=self.root/'linked.pdf';c=Canvas(str(path),pagesize=(360,240))
        c.drawString(30,180,'Old text');c.linkURL('https://example.org/',(25,175,110,195));c.save();original=path.read_bytes()
        ws=Workspace();ws.add_sources([load_source(path)]);before=tuple(ws.pages)
        with self.assertRaisesRegex(PdfWorkbenchError,'Link'):
            prepare_text_replacement(ws.sources,ws.pages,ws.pages[0].uid,'0','New')
        self.assertEqual(tuple(ws.pages),before);self.assertEqual(path.read_bytes(),original)
        with mupdf().open(path) as doc:self.assertEqual(doc[0].get_links()[0]['uri'],'https://example.org/')

    def test_text_rejects_internal_links_outside_the_edited_row(self):
        path=self.root/'internal.pdf';c=Canvas(str(path),pagesize=(360,240))
        c.drawString(30,180,'Old text');c.drawString(30,80,'Unrelated link')
        c.linkRect('Go','second',(25,70,130,90),relative=0)
        c.showPage();c.bookmarkPage('second');c.drawString(30,180,'Target');c.save();original=path.read_bytes()
        ws=Workspace();ws.add_sources([load_source(path)]);before=tuple(ws.pages)
        with self.assertRaisesRegex(PdfWorkbenchError,'interne'):
            prepare_text_replacement(ws.sources,ws.pages,ws.pages[0].uid,'0','New')
        self.assertEqual(tuple(ws.pages),before);self.assertEqual(path.read_bytes(),original)
        with mupdf().open(path) as doc:self.assertEqual(doc[0].get_links()[0]['page'],1)

    def test_text_rejects_disjoint_widgets_without_orphaning_form_tree(self):
        path=self.root/'form.pdf';c=Canvas(str(path),pagesize=(360,240))
        c.drawString(30,180,'Old text');c.acroForm.textfield(name='field1',x=180,y=30,width=120,height=25,value='Keep form');c.showPage();c.save();original=path.read_bytes()
        ws=Workspace();ws.add_sources([load_source(path)]);before=tuple(ws.pages)
        with self.assertRaisesRegex(PdfWorkbenchError,'Formularfelder'):
            prepare_text_replacement(ws.sources,ws.pages,ws.pages[0].uid,'0','New')
        self.assertEqual(tuple(ws.pages),before);self.assertEqual(path.read_bytes(),original)
        self.assertEqual(PdfReader(path).get_fields()['field1']['/V'],'Keep form')
        exported=self.root/'intact-form.pdf';export_pdf(ws.sources,ws.pages,exported)
        self.assertEqual(next(iter(PdfReader(exported).get_fields().values()))['/V'],'Keep form')

    def test_text_retains_disjoint_uri_links_and_workbench_object_models(self):
        path=self.root/'uri.pdf';c=Canvas(str(path),pagesize=(360,240))
        c.drawString(30,180,'Old text');c.linkURL('https://example.org/',(30,30,130,55));c.showPage();c.save()
        ws=Workspace();ws.add_sources([load_source(path)]);uid=ws.pages[0].uid
        annotation=PageAnnotation('own-text','text',(.1,.4,.4,.5),'Keep own');ws.add_annotation(uid,annotation)
        result=prepare_text_replacement(ws.sources,ws.pages,uid,'0','New')
        self.assertEqual(result.pages[0].annotations,(annotation,));ws.apply_page_replacements(result.sources,result.pages)
        exported=self.root/'uri-edited.pdf';export_pdf(ws.sources,ws.pages,exported)
        self.assertEqual(load_source(exported).page_annotations[0],(annotation,))
        with mupdf().open(exported) as doc:self.assertEqual(doc[0].get_links()[0]['uri'],'https://example.org/')

    def test_redaction_second_renderer_sees_real_black_pixels(self):
        if not shutil.which('pdftoppm'):self.skipTest('Poppler is unavailable')
        out=export_redacted_pdf(self.workspace.sources,self.workspace.pages,self.root/'second.pdf',[self.region()],confirmed=True)
        subprocess.run(['pdftoppm','-r','72','-singlefile','-png',str(out.path),str(self.root/'render')],check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        from PIL import Image
        with Image.open(self.root/'render.png') as image:self.assertEqual(image.convert('RGB').getpixel((60,55)),(0,0,0))

    def test_pdfa_never_publishes_bytes_changed_during_real_validation_boundary(self):
        data=workspace_pdf_bytes(self.workspace.sources,self.workspace.pages);target=self.root/'unstable-pdfa.pdf'
        def fake_converter(command,cancel=None,timeout=240):
            out=next(argument for argument in command if argument.startswith('-sOutputFile='))
            Path(out.split('=',1)[1]).write_bytes(data);return 0,'',''
        def changed_validator(path,**kwargs):
            Path(path).write_bytes(data+b'\n% changed after first read\n')
            return PdfaValidation(True,'2b','test report')
        with patch('pdf_workbench.pdfa.discover_ghostscript',return_value='gs'),patch('pdf_workbench.pdfa.discover_verapdf',return_value='verapdf'),patch('pdf_workbench.pdfa._run',side_effect=fake_converter),patch('pdf_workbench.pdfa.validate_pdfa',side_effect=changed_validator):
            with self.assertRaisesRegex(PdfWorkbenchError,'während'):
                export_pdfa(self.workspace.sources,self.workspace.pages,target)
        self.assertFalse(target.exists())


class CertificateTests(unittest.TestCase):
    setUp = AdvancedPdfTests.setUp
    tearDown = AdvancedPdfTests.tearDown
    def certificate(self):
        from cryptography import x509
        from cryptography.x509.oid import NameOID
        from cryptography.hazmat.primitives import hashes,serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.hazmat.primitives.serialization.pkcs12 import serialize_key_and_certificates
        key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'PDF Workbench local test')]);now=datetime.now(timezone.utc)
        cert=(x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now-timedelta(days=1)).not_valid_after(now+timedelta(days=3))
            .add_extension(x509.BasicConstraints(ca=True,path_length=None),True)
            .add_extension(x509.KeyUsage(True,True,False,False,False,True,True,None,None),True).sign(key,hashes.SHA256()))
        pfx=self.root/'test.p12';pfx.write_bytes(serialize_key_and_certificates(b'test',key,cert,None,serialization.BestAvailableEncryption(b'ram-only')))
        pem=self.root/'test.pem';pem.write_bytes(cert.public_bytes(serialization.Encoding.PEM));return pfx,pem

    def test_signature_integrity_offline_trust_and_independent_pdfsig(self):
        pfx,pem=self.certificate();ws=self.workspace
        result=sign_pdf_certificate(ws.sources,ws.pages,self.root/'signed.pdf',pfx,'ram-only')
        self.assertTrue(result.status.integrity_ok);self.assertFalse(result.status.trusted)
        self.assertEqual(result.status.coverage,'ENTIRE_FILE');self.assertFalse(result.status.revocation_checked)
        trusted=verify_pdf_signatures(result.path,trust_roots=[pem]);self.assertTrue(trusted[0].trusted);self.assertTrue(trusted[0].integrity_ok)
        if shutil.which('pdfsig'):
            checked=subprocess.run(['pdfsig',str(result.path)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,check=True)
            self.assertIn('Signature is Valid',checked.stdout);self.assertIn('Total document signed',checked.stdout)
        data=result.path.read_bytes();changed=data.replace(b'/Producer',b'/Producfr',1);self.assertNotEqual(data,changed)
        tampered=self.root/'tampered.pdf';tampered.write_bytes(changed)
        status=verify_pdf_signatures(tampered,trust_roots=[pem])[0];self.assertFalse(status.integrity_ok)

    def test_wrong_pfx_password_and_cancel_never_save_or_expose_password(self):
        pfx,_=self.certificate();ws=self.workspace;target=self.root/'no-signature.pdf'
        with self.assertRaises(PdfWorkbenchError) as raised:sign_pdf_certificate(ws.sources,ws.pages,target,pfx,'do-not-log-this-secret')
        self.assertNotIn('do-not-log-this-secret',str(raised.exception));self.assertFalse(target.exists())
        with self.assertRaises(OperationCancelled):sign_pdf_certificate(ws.sources,ws.pages,target,pfx,'ram-only',cancel=lambda:True)
        self.assertFalse(target.exists())


class PdfaReportTests(unittest.TestCase):
    def test_only_real_single_job_pdfa2b_success_is_accepted(self):
        report='<report><jobs><job><validationReport jobEndStatus="normal" profileName="PDF/A-2b validation profile" isCompliant="true"><details passedRules="144" failedRules="0" failedChecks="0"/></validationReport></job></jobs><batchSummary totalJobs="1" failedToParse="0" encrypted="0" outOfMemory="0" veraExceptions="0"><validationReports compliant="1" nonCompliant="0" failedJobs="0">1</validationReports></batchSummary></report>'
        result=parse_verapdf_report(report);self.assertTrue(result.compliant)
        invalid=report.replace('isCompliant="true"','isCompliant="false"').replace('failedRules="0"','failedRules="1"').replace('compliant="1" nonCompliant="0"','compliant="0" nonCompliant="1"')
        self.assertFalse(parse_verapdf_report(invalid).compliant)
        for value in ('invalid','<report/>',report.replace('PDF/A-2b','PDF/A-1b'),report.replace('<details passedRules="144" failedRules="0" failedChecks="0"/>',''),report.replace('failedJobs="0"','failedJobs="1"'),report.replace('totalJobs="1"','totalJobs="2"'),report.replace('jobEndStatus="normal"','jobEndStatus="aborted"')):
            with self.assertRaises(PdfWorkbenchError):parse_verapdf_report(value)


if __name__=='__main__':unittest.main()
