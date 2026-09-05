"""Safe PDF rendering and signed, independently verifiable export bundles."""
from __future__ import annotations
import base64
import io
import json
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, KeepTogether
from .store import canonical, digest


def render_pdf(report: dict, settings) -> bytes:
    font = 'Helvetica'
    full_text = canonical(report['sections']) + canonical(report['snapshot']['case'])
    if settings.pdf_font:
        font = 'OAFConfigured'
        pdfmetrics.registerFont(TTFont(font, settings.pdf_font))
        available = pdfmetrics.getFont(font).face.charToGlyph
        missing = {c for c in full_text if ord(c) > 31 and ord(c) not in available}
        if missing:
            raise ValueError('Configured PDF font is missing glyphs used by the report')
    elif any(ord(c) > 255 for c in full_text):
        raise ValueError('Set OAF_PDF_FONT to a suitable licensed font for non-Latin-1 report text')
    buffer = io.BytesIO()
    styles = getSampleStyleSheet()
    body = ParagraphStyle('OAFBody', parent=styles['BodyText'], fontName=font, fontSize=10.5,
                          leading=15, spaceAfter=8, wordWrap='CJK')
    heading = ParagraphStyle('OAFHeading', parent=body, fontSize=12, leading=17,
                             spaceBefore=13, textColor=colors.HexColor('#163f46'))
    title = ParagraphStyle('OAFTitle', parent=body, fontSize=21, leading=26, spaceAfter=14)
    small = ParagraphStyle('OAFSmall', parent=body, fontSize=8, leading=11)
    case = report['snapshot']['case']
    def p(text, style=body):
        return Paragraph(escape(str(text)).replace('\n', '<br/>'), style)
    story = [p('OpenAutopsyFlow', title), p('Autopsy casework report - human-authored opinion', small)]
    if settings.demo:
        story += [p('SYNTHETIC DEMONSTRATION - NOT A REAL CASE OR AUTHORIZED MEDICAL REPORT', heading)]
    if report['status'] != 'issued':
        story += [p('DRAFT - NOT ISSUED', heading)]
    pairs = [('Case number', case['case_no']), ('Examination date', case['examination_date']),
             ('Examiner', case['examiner']), ('Requesting authority', case['requesting_authority']),
             ('Identification', case['identification']), ('Subject reference', case['subject_reference']),
             ('Report', f"Version {report['number']} / {report['kind']} / {report['status']}"),
             ('Source revision', report['source_revision']), ('Issued (UTC)', report.get('issued_at') or 'Not issued')]
    table = Table([[p(a, small), p(b, small)] for a, b in pairs], colWidths=[43*mm, 127*mm])
    table.setStyle(TableStyle([('VALIGN', (0,0),(-1,-1),'TOP'),
                               ('BACKGROUND',(0,0),(0,-1),colors.HexColor('#edf4f3')),
                               ('BOTTOMPADDING',(0,0),(-1,-1),6),('TOPPADDING',(0,0),(-1,-1),6)]))
    story += [table, Spacer(1, 5*mm)]
    for section in report['sections']:
        story.append(KeepTogether([p(section['title'], heading), p(section['text'] or '(Not recorded)')]))
    story += [p('Provenance and review', heading),
              p('Content digest: ' + (report.get('approved_digest') or 'Not approved'), small),
              p('Author account: ' + report['author'], small),
              p('Reviewer account: ' + str(report.get('reviewer') or 'Not approved'), small),
              p('Issued documents preserve their approved snapshot. Account approval is not a qualified '
                'electronic signature and does not establish legal admissibility.', small)]
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=20*mm, leftMargin=20*mm,
                            topMargin=18*mm, bottomMargin=20*mm, title='Casework report',
                            author='OpenAutopsyFlow', pageCompression=1)
    def footer(canvas, document):
        canvas.saveState()
        canvas.setFont('Helvetica', 8)
        canvas.setFillColor(colors.HexColor('#596c72'))
        canvas.drawString(20*mm, 12*mm, 'OpenAutopsyFlow | Casework record | Verify against the preserved original')
        canvas.drawRightString(190*mm, 12*mm, f'Page {document.page}')
        canvas.restoreState()
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()


def signed_bundle(store, files: dict[str, bytes], metadata: dict) -> bytes:
    manifest = {'format': 'openautopsyflow-bundle-v1', 'metadata': metadata,
                'files': {path: {'sha256': digest(data), 'bytes': len(data)} for path, data in sorted(files.items())}}
    raw = canonical(manifest).encode()
    signature = store.signer.sign(raw)
    result = io.BytesIO()
    with zipfile.ZipFile(result, 'w', zipfile.ZIP_DEFLATED) as archive:
        for path, data in sorted(files.items()):
            archive.writestr(path, data)
        archive.writestr('manifest.json', raw)
        archive.writestr('manifest.ed25519', base64.b64encode(signature))
        archive.writestr('public-key.txt', store.public_key())
    return result.getvalue()


def verify_bundle(data: bytes, trusted_key: str | None = None) -> dict:
    """Never extracts files. A supplied trusted key anchors identity; embedded key alone does not."""
    if len(data) > 256 * 1024 * 1024:
        raise ValueError('Bundle exceeds verifier size limit')
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        info = z.infolist()
        names = [x.filename for x in info]
        if len(names) != len(set(names)) or len(names) > 5000:
            raise ValueError('Duplicate filenames or too many entries')
        if sum(x.file_size for x in info) > 256 * 1024 * 1024:
            raise ValueError('Expanded bundle exceeds verifier size limit')
        for name in names:
            if name.startswith('/') or '\\' in name or '..' in Path(name).parts:
                raise ValueError('Unsafe archive path')
        required = {'manifest.json', 'manifest.ed25519', 'public-key.txt'}
        if not required <= set(names):
            raise ValueError('Missing manifest/signature/key')
        raw = z.read('manifest.json')
        key = z.read('public-key.txt').decode().strip()
        if trusted_key is not None and key != trusted_key.strip():
            raise ValueError('Signing key does not match trusted key')
        Ed25519PublicKey.from_public_bytes(base64.b64decode(key, validate=True)).verify(
            base64.b64decode(z.read('manifest.ed25519'), validate=True), raw)
        manifest = json.loads(raw)
        if manifest.get('format') != 'openautopsyflow-bundle-v1' or canonical(manifest).encode() != raw:
            raise ValueError('Unknown format or non-canonical manifest')
        if set(names) != required | set(manifest['files']):
            raise ValueError('Unlisted or missing bundle entries')
        for path, expected in manifest['files'].items():
            blob = z.read(path)
            if digest(blob) != expected['sha256'] or len(blob) != expected['bytes']:
                raise ValueError('Integrity failure: ' + path)
        return {'integrity_verified': True, 'identity_anchored': trusted_key is not None,
                'files_verified': len(manifest['files']), 'public_key': key,
                'warning': None if trusted_key else 'Embedded key verifies internal consistency only; pin a trusted key out of band.'}
