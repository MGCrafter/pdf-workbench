"""Publisher identity, stable-version policy and bounded, verified downloads."""
from dataclasses import replace
import hashlib,io,json,os
from pathlib import Path
import tempfile,unittest
from unittest.mock import patch

from pdf_workbench.core import OperationCancelled
from pdf_workbench.updates import (API_URL,MAX_METADATA,REPOSITORY,DownloadedUpdate,ReleaseAsset,UpdateError,
    UpdateRelease,_Redirects,_https_url,check_latest,download_update,release_from_metadata,should_check,version_tuple)


def metadata(version="0.8.0",target="linux",payload=b"archive"):
    name=f"PDFWorkbench-v{version}-{target}-x86_64"+(".zip" if target=="windows" else ".tar.gz")
    digest=hashlib.sha256(payload).hexdigest()
    checksums=(f"{digest}  PDFWorkbench-v{version}-linux-x86_64.tar.gz\n"+f"{digest}  PDFWorkbench-v{version}-windows-x86_64.zip\n").encode()
    def asset(name,data):return {"name":name,"browser_download_url":f"https://github.com/{REPOSITORY}/releases/download/v{version}/{name}","size":len(data),"digest":"sha256:"+hashlib.sha256(data).hexdigest(),"state":"uploaded"}
    return {"tag_name":"v"+version,"html_url":f"https://github.com/{REPOSITORY}/releases/tag/v{version}","draft":False,"prerelease":False,"assets":[asset(name,payload),asset("SHA256SUMS",checksums)]},checksums


class Response(io.BytesIO):
    def __init__(self,data,headers=None):super().__init__(data);self.headers=headers or {}


class UpdateTests(unittest.TestCase):
    def setUp(self):self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
    def tearDown(self):self.temp.cleanup()

    def test_release_selects_exact_native_asset_and_never_downgrades(self):
        data,_=metadata();release=release_from_metadata(data,"0.7.99","linux")
        self.assertEqual(release.version,"0.8.0");self.assertEqual(release.platform,"linux")
        self.assertIsNone(release_from_metadata(data,"0.8.0","linux"));self.assertIsNone(release_from_metadata(data,"1.0.0","linux"))
        with self.assertRaises(UpdateError):release_from_metadata(data,"0.7.0","windows")
        self.assertGreater(version_tuple("0.10.0"),version_tuple("0.9.9"))

    def test_prerelease_draft_wrong_repository_duplicates_missing_digest_rejected(self):
        for mutate in (lambda d:d.update(prerelease=True),lambda d:d.update(draft=True),lambda d:d.update(tag_name="v0.8.0-rc1"),
                       lambda d:d.update(html_url="https://github.com/elsewhere/repo/releases/tag/v0.8.0"),
                       lambda d:d["assets"].append(dict(d["assets"][0])),lambda d:d["assets"][0].pop("digest"),
                       lambda d:d["assets"][0].update(size=2**40),lambda d:d["assets"][0].update(browser_download_url="https://github.com/elsewhere/wrong.zip")):
            data,_=metadata();mutate(data)
            with self.subTest(data=data),self.assertRaises(UpdateError):release_from_metadata(data,"0.7.0","linux")

    def test_release_request_is_small_and_cancelable(self):
        data,_=metadata()
        with patch("pdf_workbench.updates._open",return_value=Response(json.dumps(data).encode())) as opened:
            self.assertEqual(check_latest("0.7.0","linux").version,"0.8.0");opened.assert_called_once_with(API_URL)
        with patch("pdf_workbench.updates._open",return_value=Response(b"x"*(MAX_METADATA+1))),self.assertRaises(UpdateError):check_latest("0.7.0","linux")
        with self.assertRaises(OperationCancelled):check_latest("0.7.0",cancel=lambda:True)

    def test_redirects_require_https_exact_hosts_no_userinfo_ports_or_fragments(self):
        for url in ("http://github.com/file","https://github.com.evil.test/file","https://user@github.com/file","https://github.com:444/file","https://github.com/file#fragment","https://127.0.0.1/file"):
            with self.subTest(url=url),self.assertRaises(UpdateError):_https_url(url)
        self.assertEqual(_https_url("https://release-assets.githubusercontent.com/file?token=abc"),"https://release-assets.githubusercontent.com/file?token=abc")
        with self.assertRaises(UpdateError):_Redirects().redirect_request(None,None,302,"",{},"https://evil.test/payload")

    def test_download_checks_both_hashes_exact_size_and_reuses_valid_cache(self):
        payload=b"real package bytes";data,checksums=metadata(payload=payload);release=release_from_metadata(data,"0.7.0","linux")
        with patch("pdf_workbench.updates.native_platform",return_value="linux"),patch("pdf_workbench.updates._open",side_effect=[Response(checksums),Response(payload,{"Content-Length":str(len(payload))})]):
            result=download_update(release,self.root);self.assertEqual(result.archive.read_bytes(),payload)
        with patch("pdf_workbench.updates.native_platform",return_value="linux"),patch("pdf_workbench.updates._open",side_effect=[Response(checksums)]) as opened:
            reused=download_update(release,self.root);self.assertEqual(reused,result);self.assertEqual(opened.call_count,1)

    def test_mismatched_metadata_checksums_truncated_oversized_cancel_remove_partial(self):
        payload=b"abc";data,checksums=metadata(payload=payload);release=release_from_metadata(data,"0.7.0","linux")
        for body in (b"wrong",b"ab",b"abcd"):
            with patch("pdf_workbench.updates.native_platform",return_value="linux"),patch("pdf_workbench.updates._open",side_effect=[Response(checksums),Response(body)]),self.assertRaises(UpdateError):download_update(release,self.root)
            self.assertEqual(list(self.root.iterdir()),[])
        with patch("pdf_workbench.updates.native_platform",return_value="linux"),patch("pdf_workbench.updates._open",return_value=Response(checksums+b"x")),self.assertRaises(UpdateError):download_update(release,self.root)
        inconsistent=checksums.replace(hashlib.sha256(payload).hexdigest().encode(),b"0"*64)
        fake=replace(release,checksums=replace(release.checksums,sha256=hashlib.sha256(inconsistent).hexdigest()))
        with patch("pdf_workbench.updates.native_platform",return_value="linux"),patch("pdf_workbench.updates._open",return_value=Response(inconsistent)),self.assertRaises(UpdateError):download_update(fake,self.root)
        with self.assertRaises(OperationCancelled),patch("pdf_workbench.updates.native_platform",return_value="linux"):download_update(release,self.root,cancel=lambda:True)

    def test_daily_check_clock_and_disabled_preference_are_separate(self):
        self.assertTrue(should_check(None,100000));self.assertTrue(should_check("invalid",100000));self.assertFalse(should_check(99999,100000));self.assertTrue(should_check(1000,100000));self.assertTrue(should_check(float("nan"),100000));self.assertTrue(should_check(200000,100000))

    def test_windows_cache_semicolon_fails_before_network_and_keeps_user_bytes(self):
        data,checksums=metadata(target="windows");release=release_from_metadata(data,"0.7.99","windows")
        cache=self.root/"Eigene Ä; Daten";cache.mkdir();original=cache/"Projekt; geschützt.pdfwb";original.write_bytes(b"ORIGINAL USER PROJECT")
        with patch("pdf_workbench.updates.native_platform",return_value="windows"),patch("pdf_workbench.updates._open") as network:
            with self.assertRaisesRegex(UpdateError,"Semikolon"):download_update(release,cache)
            network.assert_not_called()
        self.assertEqual(original.read_bytes(),b"ORIGINAL USER PROJECT");self.assertEqual(list(cache.iterdir()),[original])
        allowed=self.root/"Eigene Ä Daten $(literal)"
        with patch("pdf_workbench.updates.native_platform",return_value="windows"),patch("pdf_workbench.updates._open",side_effect=[Response(checksums),Response(b"archive")]):
            self.assertEqual(download_update(release,allowed).archive.read_bytes(),b"archive")


if __name__=="__main__":unittest.main()
