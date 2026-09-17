"""Render the invoice print page to PDF with headless Chrome.

The PDF is a print of templates/invoices/print.html, which includes the same
markup and CSS partials the live preview uses — so preview and PDF cannot drift
apart the way a separate ReportLab layout did.

Chrome is located rather than assumed; when none is present the caller falls
back to the ReportLab path so PDF generation never hard-fails.
"""
import os
import shutil
import subprocess
import tempfile
import logging

logger = logging.getLogger(__name__)

_CANDIDATES = [
    os.environ.get('CHROME_BIN'),
    r'C:\Program Files\Google\Chrome\Application\chrome.exe',
    r'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe',
    r'C:\Program Files\Microsoft\Edge\Application\msedge.exe',
    r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
    '/usr/bin/google-chrome', '/usr/bin/chromium', '/usr/bin/chromium-browser',
]


def find_chrome():
    for c in _CANDIDATES:
        if c and os.path.exists(c):
            return c
    for name in ('google-chrome', 'chromium', 'chromium-browser', 'chrome', 'msedge'):
        p = shutil.which(name)
        if p:
            return p
    return None


def html_to_pdf(html, out_path, timeout=60):
    """Print an HTML string to `out_path`. Returns True on success.

    --virtual-time-budget lets the page's font/image await settle before the
    snapshot, which is what keeps the logo and signature from being missing.
    """
    chrome = find_chrome()
    if not chrome:
        logger.warning("no Chrome/Edge binary found — cannot render HTML to PDF")
        return False

    tmpdir = tempfile.mkdtemp(prefix='invpdf_')
    src = os.path.join(tmpdir, 'invoice.html')
    with open(src, 'w', encoding='utf-8') as fh:
        fh.write(html)

    cmd = [
        chrome, '--headless=new', '--disable-gpu', '--no-sandbox',
        '--no-pdf-header-footer',
        '--run-all-compositor-stages-before-draw',
        '--virtual-time-budget=8000',
        f'--user-data-dir={os.path.join(tmpdir, "prof")}',
        f'--print-to-pdf={out_path}',
        'file:///' + src.replace('\\', '/'),
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout)
        if not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
            logger.warning("chrome print produced no file: %s",
                           (r.stderr or b'')[:400])
            return False
        return True
    except Exception:
        logger.warning("chrome print failed", exc_info=True)
        return False
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
