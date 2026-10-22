"""Offline certificate signatures with private keys and passwords kept in RAM.

Integrity, trust and revocation are deliberately separate results. Without an
explicit user-supplied trust root this tool never calls a certificate trusted.
It performs no network lookups, timestamp requests or qualified-signature claims.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import io
from pathlib import Path

from .advanced_pdf import publish_new_pdf, workspace_pdf_bytes
from .core import PdfWorkbenchError, _check_cancel


@dataclass(frozen=True)
class SignatureStatus:
    field_name: str
    signer: str
    intact: bool
    valid: bool
    trusted: bool
    coverage: str
    modified_after_signing: bool
    details: str
    revocation_checked: bool = False

    @property
    def integrity_ok(self):
        return self.intact and self.valid


@dataclass(frozen=True)
class CertificateSignatureResult:
    path: Path
    status: SignatureStatus


def _signature_modules():
    try:
        from pyhanko.sign import signers
        from pyhanko.sign.fields import SigFieldSpec
        from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
        from pyhanko.pdf_utils.reader import PdfFileReader
        from pyhanko.sign.validation import validate_pdf_signature
        from pyhanko_certvalidator import ValidationContext
        return signers, SigFieldSpec, IncrementalPdfFileWriter, PdfFileReader, validate_pdf_signature, ValidationContext
    except ImportError:
        raise PdfWorkbenchError("Zertifikatssignaturen benötigen pyHanko. Bitte die vollständige PDF-Workbench-Installation verwenden.") from None


def verify_pdf_signatures(document, *, trust_roots=None, cancel=None, progress=None):
    """Verify bytes or a PDF path offline against explicitly supplied PEM roots.

    A cryptographic integrity success does not imply a trusted identity, checked
    revocation, a qualified electronic signature or a trusted signing time.
    """
    _, _, _, Reader, validate, ValidationContext = _signature_modules()
    _check_cancel(cancel)
    if isinstance(document, (str, Path)):
        try: data = Path(document).expanduser().read_bytes()
        except OSError: raise PdfWorkbenchError("Die PDF für die Signaturprüfung konnte nicht gelesen werden.") from None
    elif isinstance(document, bytes): data = document
    else: raise PdfWorkbenchError("Die Signaturprüfung benötigt eine PDF-Datei.")
    roots = []
    if trust_roots:
        from pyhanko.keys import load_certs_from_pemder
        try: roots = list(load_certs_from_pemder([str(Path(path).expanduser()) for path in trust_roots]))
        except Exception: raise PdfWorkbenchError("Die angegebenen Vertrauenszertifikate konnten nicht gelesen werden.") from None
    try:
        reader = Reader(io.BytesIO(data))
        if reader.encrypted:
            raise PdfWorkbenchError("Für die direkte Signaturprüfung bitte eine unverschlüsselte PDF verwenden. Entschlüsseln oder Neu-Exportieren würde bestehende Signaturen ändern.")
        signatures = reader.embedded_signatures
        results = []
        for index, signature in enumerate(signatures):
            _check_cancel(cancel)
            context = ValidationContext(trust_roots=roots, allow_fetching=False, revocation_mode="soft-fail")
            try:
                status = validate(signature, signer_validation_context=context)
                coverage = getattr(status.coverage, "name", str(status.coverage))
                modified = coverage != "ENTIRE_FILE"
                details = status.pretty_print_details()
                results.append(SignatureStatus(str(signature.field_name), signature.signer_cert.subject.human_friendly,
                                                bool(status.intact), bool(status.valid), bool(status.trusted),
                                                coverage, modified, details))
            except Exception:
                # Unsupported CMS, byte ranges or malformed signatures must be
                # visible failures, not disappear from the report.
                results.append(SignatureStatus(str(signature.field_name), "Unbekannt", False, False, False,
                                                "UNBEKANNT", True, "Diese Signatur konnte nicht vollständig geprüft werden."))
            if progress: progress(round((index + 1) * 100 / max(1, len(signatures))), "Zertifikatssignaturen werden offline geprüft …")
        _check_cancel(cancel)
        return tuple(results)
    except PdfWorkbenchError: raise
    except Exception:
        raise PdfWorkbenchError("Die PDF-Signaturstruktur konnte nicht gelesen werden.") from None


def sign_pdf_certificate(sources, pages, destination, certificate, password, *, reason="",
                         cancel=None, progress=None, bookmarks=None):
    """Sign a fresh workspace export with the user's PKCS#12 certificate.

    The signature is invisible and covers the complete new file. The source
    certificates' trust is not inferred from possession of its private key.
    """
    signers, SigFieldSpec, IncrementalWriter, _, _, _ = _signature_modules()
    if not isinstance(password, str): raise PdfWorkbenchError("Bitte das Zertifikatspasswort eingeben.")
    if not isinstance(reason, str) or len(reason) > 1000:
        raise PdfWorkbenchError("Der Signaturgrund darf höchstens 1000 Zeichen enthalten.")
    _check_cancel(cancel)
    try:
        from cryptography.hazmat.primitives.serialization.pkcs12 import load_key_and_certificates
        from cryptography.x509 import KeyUsage, ExtensionNotFound
        raw = Path(certificate).expanduser().read_bytes()
        encoded_password = password.encode("utf-8") if password else None
        key, cert, _ = load_key_and_certificates(raw, encoded_password)
        if key is None or cert is None: raise ValueError()
        now = datetime.now(timezone.utc)
        if not cert.not_valid_before_utc <= now <= cert.not_valid_after_utc:
            raise PdfWorkbenchError("Das Signaturzertifikat ist noch nicht oder nicht mehr zeitlich gültig.")
        try:
            usage = cert.extensions.get_extension_for_class(KeyUsage).value
            if not usage.digital_signature and not usage.content_commitment:
                raise PdfWorkbenchError("Dieses Zertifikat erlaubt keine digitalen Dokumentensignaturen.")
        except ExtensionNotFound: pass
        signer = signers.SimpleSigner.load_pkcs12_data(raw, other_certs=(), passphrase=encoded_password)
        if signer is None: raise ValueError()
    except PdfWorkbenchError: raise
    except Exception:
        # Password and private material must never enter user errors or logs.
        raise PdfWorkbenchError("Das Zertifikat konnte nicht entsperrt werden. Bitte PFX/P12-Datei, privaten Schlüssel und Passwort prüfen.") from None
    _check_cancel(cancel)
    data = workspace_pdf_bytes(sources, pages, cancel, bookmarks=bookmarks)
    if progress: progress(55, "Neue PDF wird mit dem Zertifikat signiert …")
    try:
        metadata = signers.PdfSignatureMetadata(field_name="PDFWorkbenchSignature", md_algorithm="sha256", reason=reason or None)
        output = io.BytesIO()
        signers.sign_pdf(IncrementalWriter(io.BytesIO(data)), metadata, signer=signer,
                         new_field_spec=SigFieldSpec("PDFWorkbenchSignature", on_page=0), output=output)
        signed = output.getvalue()
    except Exception:
        raise PdfWorkbenchError("Die Zertifikatssignatur konnte nicht erzeugt werden. Bitte ein unterstütztes RSA- oder EC-Zertifikat verwenden.") from None
    finally:
        # Python cannot guarantee a zeroised immutable bytes allocation; no
        # passphrase, key material or certificate path is persisted by this API.
        raw = encoded_password = key = signer = None
    _check_cancel(cancel)
    if progress: progress(85, "Signatur und Dateiintegrität werden geprüft …")
    statuses = verify_pdf_signatures(signed, cancel=cancel)
    if len(statuses) != 1 or not statuses[0].integrity_ok or statuses[0].modified_after_signing:
        raise PdfWorkbenchError("Die erzeugte Signatur hat die Integritätsprüfung nicht bestanden. Es wurde keine Datei gespeichert.")
    path = publish_new_pdf(signed, destination, sources, cancel, expected_pages=len(pages))
    if progress: progress(100, "Signierte neue PDF gespeichert")
    return CertificateSignatureResult(path, statuses[0])
