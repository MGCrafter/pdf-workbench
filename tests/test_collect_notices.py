"""Deterministic transport retries retain every fail-closed release source gate."""
from __future__ import annotations

from contextlib import nullcontext
from concurrent.futures import ThreadPoolExecutor
import errno
import hashlib
import http.client
import importlib.util
import io
import json
from pathlib import Path
import socket
import ssl
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.error

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location("retry_notices",ROOT/"packaging/collect_notices.py")
notices=importlib.util.module_from_spec(spec)
spec.loader.exec_module(notices)
URL="https://example.invalid/demo_1.0.orig.tar.xz"


class Response(io.BytesIO):
    def __init__(self,data=b"valid metadata",*,url=URL,headers=None):
        super().__init__(data)
        self.url=url
        self.headers=headers or {}


class InterruptedResponse(Response):
    def __init__(self,*,error=None):
        super().__init__()
        self.error=error or ConnectionResetError(errno.ECONNRESET,"connection reset")
        self.calls=0

    def read(self,size=-1):
        self.calls+=1
        if self.calls==1:
            return b"discarded old partial bytes"
        raise self.error


class CollectorRetryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.directory=Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)
        self.sleep=self.enterContext(patch.object(notices.time,"sleep"))
        self.log=io.StringIO()
        self.enterContext(patch.object(notices.sys,"stderr",self.log))

    def archive(self,data=b"complete source archive",*,digest=True,size=None):
        return {"filename":"demo.tar.xz","url":URL,
                "sha256":hashlib.sha256(data).hexdigest() if digest else None,
                "size":len(data) if size is None else size}

    def http_error(self,status,headers=None):
        return urllib.error.HTTPError(URL,status,"temporary fixture",headers or {},io.BytesIO(b"do not log this response body"))

    def test_each_transient_http_status_reopens_and_returns_verified_metadata(self):
        for status in (429,500,502,503,504):
            with self.subTest(status=status):
                self.sleep.reset_mock();self.log.seek(0);self.log.truncate()
                error=self.http_error(status)
                with patch.object(notices.urllib.request,"urlopen",side_effect=[error,Response(b"success")]) as opened:
                    self.assertEqual(notices.fetch(URL),b"success")
                self.assertEqual(opened.call_count,2)
                self.sleep.assert_called_once_with(2)
                self.assertTrue(error.fp.closed)
                self.assertIn(URL,self.log.getvalue())
                self.assertIn("retry 1/4",self.log.getvalue())
                self.assertNotIn("do not log this response body",self.log.getvalue())

    def test_transport_disconnect_timeout_and_temporary_dns_are_retried(self):
        errors=(TimeoutError("timeout"),ConnectionResetError(errno.ECONNRESET,"reset"),
                http.client.RemoteDisconnected("closed"),http.client.IncompleteRead(b"part",10),
                urllib.error.URLError(socket.gaierror(socket.EAI_AGAIN,"temporary DNS")),
                urllib.error.URLError(ConnectionAbortedError(errno.ECONNABORTED,"aborted")),
                ssl.SSLEOFError(8,"TLS EOF"),OSError(errno.ENETUNREACH,"network unreachable"))
        for error in errors:
            with self.subTest(error=repr(error)):
                self.sleep.reset_mock()
                with patch.object(notices.urllib.request,"urlopen",side_effect=[error,Response(b"ok")]) as opened:
                    self.assertEqual(notices.fetch(URL),b"ok")
                self.assertEqual(opened.call_count,2)
                self.sleep.assert_called_once_with(2)

    def test_not_found_certificates_permanent_dns_and_validation_fail_immediately(self):
        errors=(self.http_error(404),self.http_error(403),self.http_error(501),
                urllib.error.URLError(ssl.SSLCertVerificationError(1,"certificate verify failed")),
                ssl.SSLCertVerificationError(1,"certificate verify failed"),
                urllib.error.URLError(socket.gaierror(socket.EAI_NONAME,"unknown host")))
        for error in errors:
            with self.subTest(error=repr(error)):
                self.sleep.reset_mock()
                with patch.object(notices.urllib.request,"urlopen",side_effect=error) as opened:
                    with self.assertRaises(notices.IncompleteNotices) as caught:
                        notices.fetch(URL)
                self.assertEqual(opened.call_count,1)
                self.sleep.assert_not_called()
                self.assertIn(URL,str(caught.exception))
                self.assertIs(caught.exception.__cause__,error)
        self.sleep.reset_mock()
        for response in (Response(b"",url=URL),Response(b"toolarge",url=URL),
                         Response(b"ok",url="http://example.invalid/insecure"),
                         Response(b"ok",headers={"Content-Length":"invalid"})):
            with self.subTest(response=response):
                with patch.object(notices.urllib.request,"urlopen",return_value=response) as opened:
                    with self.assertRaises(notices.IncompleteNotices):
                        notices.fetch(URL,limit=4)
                self.assertEqual(opened.call_count,1)
                self.sleep.assert_not_called()

    def test_retries_are_bounded_and_exhausted_error_names_source_and_attempts(self):
        def unavailable(*args,**kwargs):
            raise self.http_error(503)
        with patch.object(notices.urllib.request,"urlopen",side_effect=unavailable) as opened:
            with self.assertRaises(notices.IncompleteNotices) as caught:
                notices.fetch(URL)
        self.assertEqual(opened.call_count,5)
        self.assertEqual([call.args[0] for call in self.sleep.call_args_list],[2,4,8,16])
        self.assertIn("after 5 attempts",str(caught.exception))
        self.assertIn(URL,str(caught.exception))
        self.assertIn("503",str(caught.exception))
        self.assertIsInstance(caught.exception.__cause__,urllib.error.HTTPError)

    def test_retry_after_is_respected_but_capped_and_invalid_values_use_backoff(self):
        for value,expected in (("7",7),("600",30),("not a date",2),
                               ("Wed, 21 Oct 2015 07:28:09 GMT",9)):
            with self.subTest(retry_after=value):
                self.sleep.reset_mock()
                error=self.http_error(429,{"Retry-After":value})
                with patch.object(notices.time,"time",return_value=1445412480),patch.object(notices.urllib.request,"urlopen",side_effect=[error,Response(b"ok")]):
                    self.assertEqual(notices.fetch(URL),b"ok")
                self.sleep.assert_called_once_with(expected)

    def test_truncated_metadata_is_retried_with_a_fresh_response(self):
        bad=Response(b"partial",headers={"Content-Length":"100"})
        with patch.object(notices.urllib.request,"urlopen",side_effect=[bad,Response(b"complete",headers={"Content-Length":"8"})]) as opened:
            self.assertEqual(notices.fetch(URL),b"complete")
        self.assertTrue(bad.closed)
        self.assertEqual(opened.call_count,2)
        self.sleep.assert_called_once_with(2)

    def test_archive_retry_discards_partial_and_restarts_hash_for_known_and_calculated_digests(self):
        data=b"complete source archive"
        for known_digest in (True,False):
            with self.subTest(known_digest=known_digest):
                destination=self.directory/str(known_digest);destination.mkdir()
                partial=destination/"demo.tar.xz.partial"
                partial.write_bytes(b"stale previous process")
                item=self.archive(data,digest=known_digest)
                broken=InterruptedResponse()
                calls=0
                def opened(*args,**kwargs):
                    nonlocal calls
                    calls+=1
                    self.assertFalse(partial.exists())
                    if calls==1:
                        return broken
                    return Response(data,headers={"Content-Length":str(len(data))})
                with patch.object(notices.urllib.request,"urlopen",side_effect=opened):
                    notices.download_sources([item],destination)
                self.assertEqual(calls,2)
                self.assertTrue(broken.closed)
                self.assertFalse(partial.exists())
                self.assertEqual((destination/'demo.tar.xz').read_bytes(),data)
                self.assertEqual(item['sha256'],hashlib.sha256(data).hexdigest())
                self.assertEqual(json.loads((destination/'source-manifest.json').read_text())[0],item)

    def test_truncated_archive_reopens_instead_of_resuming(self):
        data=b"complete source archive"
        item=self.archive(data)
        responses=[Response(b"partial",headers={"Content-Length":str(len(data))}),Response(data)]
        with patch.object(notices.urllib.request,"urlopen",side_effect=responses) as opened:
            notices.download_sources([item],self.directory)
        self.assertEqual(opened.call_count,2)
        self.assertEqual((self.directory/'demo.tar.xz').read_bytes(),data)
        self.assertFalse((self.directory/'demo.tar.xz.partial').exists())

    def test_archive_hash_and_expected_size_failures_are_not_retried_or_published(self):
        data=b"valid-looking wrong archive"
        for field in ('sha256','size'):
            with self.subTest(field=field):
                destination=self.directory/field;destination.mkdir()
                path=destination/'demo.tar.xz';path.write_bytes(b"previous file must survive")
                (destination/'source-manifest.json').write_text('stale success')
                item=self.archive(data)
                item[field]='0'*64 if field=='sha256' else len(data)+1
                original=dict(item)
                self.sleep.reset_mock()
                with patch.object(notices.urllib.request,"urlopen",return_value=Response(data)) as opened:
                    with self.assertRaisesRegex(notices.IncompleteNotices,'mismatch'):
                        notices.download_sources([item],destination)
                self.assertEqual(opened.call_count,1)
                self.sleep.assert_not_called()
                self.assertEqual(path.read_bytes(),b"previous file must survive")
                self.assertFalse((destination/'demo.tar.xz.partial').exists())
                self.assertFalse((destination/'source-manifest.json').exists())
                self.assertEqual(item,original)

    def test_exhausted_archive_transport_never_publishes_partial_or_manifest(self):
        destination=self.directory
        path=destination/'demo.tar.xz';path.write_bytes(b"previous file")
        (destination/'source-manifest.json').write_text('stale success')
        with patch.object(notices.urllib.request,"urlopen",side_effect=lambda *a,**k: InterruptedResponse()):
            with self.assertRaisesRegex(notices.IncompleteNotices,'after 5 attempts'):
                notices.download_sources([self.archive()],destination)
        self.assertEqual(path.read_bytes(),b"previous file")
        self.assertFalse((destination/'demo.tar.xz.partial').exists())
        self.assertFalse((destination/'source-manifest.json').exists())
        self.assertEqual(self.sleep.call_count,4)

    def test_archive_https_validation_and_local_write_failure_are_not_retried(self):
        for insecure in (True,False):
            with self.subTest(insecure=insecure):
                destination=self.directory/str(insecure)
                response=Response(b"bytes",url="http://example.invalid/bad") if insecure else Response(b"bytes")
                self.sleep.reset_mock()
                with patch.object(notices.urllib.request,"urlopen",return_value=response) as opened:
                    with patch.object(notices.Path,"open",side_effect=PermissionError("local destination denied")) if not insecure else nullcontext():
                        with self.assertRaises((notices.IncompleteNotices,PermissionError)):
                            notices.download_sources([self.archive()],destination)
                self.assertEqual(opened.call_count,1)
                self.sleep.assert_not_called()
                self.assertFalse((destination/'demo.tar.xz.partial').exists())
                self.assertFalse((destination/'source-manifest.json').exists())

    def test_invalid_exact_version_descriptor_does_not_retry_metadata_validation(self):
        publication={'entries':[{'source_package_name':'demo','source_package_version':'1.0',
                                 'self_link':'https://api.launchpad.net/source-publication/demo'}]}
        urls=['https://launchpad.net/demo_1.0.dsc','https://launchpad.net/demo_1.0.orig.tar.xz']
        responses=[Response(json.dumps(publication).encode()),Response(json.dumps(urls).encode()),
                   Response(b'Source: demo\nVersion: 2.0-wrong\nChecksums-Sha256:\n '+b'a'*64+b' 123 demo_1.0.orig.tar.xz\n')]
        with patch.object(notices.urllib.request,'urlopen',side_effect=responses) as opened:
            with self.assertRaisesRegex(notices.IncompleteNotices,'does not match'):
                notices._launchpad_package_sources({'source_package':'demo','source_version':'1.0'})
        self.assertEqual(opened.call_count,3)
        self.sleep.assert_not_called()

    def test_recovered_transport_does_not_retry_or_accept_a_bad_source_digest(self):
        data=b'wrong after successful HTTP retry'
        item=self.archive(b'expected source bytes')
        with patch.object(notices.urllib.request,'urlopen',side_effect=[self.http_error(503),Response(data),Response(b'expected source bytes')]) as opened:
            with self.assertRaisesRegex(notices.IncompleteNotices,'digest mismatch'):
                notices.download_sources([item],self.directory)
        self.assertEqual(opened.call_count,2)
        self.sleep.assert_called_once_with(2)
        self.assertFalse((self.directory/'demo.tar.xz').exists())
        self.assertFalse((self.directory/'demo.tar.xz.partial').exists())
        self.assertFalse((self.directory/'source-manifest.json').exists())

    def test_parallel_metadata_remains_four_workers_with_deterministic_output(self):
        barrier=threading.Barrier(4)
        lock=threading.Lock()
        active=peak=0
        def package_sources(package):
            nonlocal active,peak
            with lock:
                active+=1;peak=max(peak,active)
            try:
                barrier.wait(timeout=5)
                name=package['source_package']
                return [{'filename':name+'.dsc','sha256':'a'*64}]
            finally:
                with lock:active-=1
        packages=[{'source_package':name,'source_version':'1'} for name in ('h','g','f','e','d','c','b','a')]
        with patch.object(notices,'_ubuntu_package_sources',side_effect=package_sources),patch.object(notices.sys,'stdout',io.StringIO()),patch.object(notices,'ThreadPoolExecutor',wraps=ThreadPoolExecutor) as pool:
            result=notices.ubuntu_source_archives({'schema_version':1,'packages':packages})
        pool.assert_called_once_with(max_workers=4)
        self.assertEqual(peak,4)
        self.assertEqual([item['filename'] for item in result],[name+'.dsc' for name in 'abcdefgh'])


if __name__=='__main__':unittest.main()
