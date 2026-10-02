"""Read an uploaded invoice design and suggest a template for it.

Used by the superadmin "Add Invoice" page: upload a PDF or image of an invoice
you like, and this picks the closest built-in layout, the brand colour and a
name, so the new template only needs a review and "Apply".

No AI service involved — it measures the page itself:
  • colour   — the most common strongly-coloured hue on the page
  • layout   — where the colour sits: a full-width band across the top
               (dark → Modern, coloured → Bold), a coloured left column
               (Sidebar), barely any colour (Minimal), otherwise Classic
  • purpose  — words in a PDF's text: payroll/salary → Payroll,
               staffing/employee/working days → Staffing
"""
import colorsys
import io
import os
import re
from collections import Counter

from PIL import Image

PDF_TYPES = {'application/pdf'}
IMAGE_TYPES = {'image/png', 'image/jpeg', 'image/jpg', 'image/webp'}
MAX_BYTES = 8 * 1024 * 1024


class AnalyzeError(ValueError):
    pass


def _load(data, content_type, filename):
    """Return (PIL RGB image of page 1, extracted text)."""
    name = (filename or '').lower()
    is_pdf = content_type in PDF_TYPES or name.endswith('.pdf')
    if is_pdf:
        try:
            import fitz  # PyMuPDF
            doc = fitz.open(stream=data, filetype='pdf')
            if not len(doc):
                raise AnalyzeError("That PDF has no pages.")
            page = doc[0]
            text = page.get_text() or ''
            pix = page.get_pixmap(dpi=72)
            img = Image.open(io.BytesIO(pix.tobytes('png'))).convert('RGB')
            return img, text
        except AnalyzeError:
            raise
        except Exception:
            raise AnalyzeError("Couldn't open that PDF. Try exporting it again, or upload a PNG/JPG of it.")
    if content_type in IMAGE_TYPES or name.endswith(('.png', '.jpg', '.jpeg', '.webp')):
        try:
            img = Image.open(io.BytesIO(data))
            img.load()
            return img.convert('RGB'), ''
        except Exception:
            raise AnalyzeError("Couldn't read that image. Upload a PNG, JPG or WEBP.")
    raise AnalyzeError("Upload a PDF or an image (PNG, JPG, WEBP) of the invoice design.")


def _is_colored(r, g, b):
    h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
    return s > 0.35 and v > 0.25, h, s, v


def _is_inked(r, g, b):
    """Anything that isn't near-white paper."""
    return (r + g + b) / 3 < 225


def _accent(px):
    """Dominant strong colour, averaged within its hue bucket. None if mono."""
    buckets = Counter()
    sums = {}
    for (r, g, b) in px:
        ok, h, s, v = _is_colored(r, g, b)
        if not ok:
            continue
        k = int(h * 24) % 24
        buckets[k] += 1
        a = sums.setdefault(k, [0, 0, 0])
        a[0] += r; a[1] += g; a[2] += b
    if not buckets:
        return None, 0.0
    k, n = buckets.most_common(1)[0]
    r, g, b = (round(c / n) for c in sums[k])
    return '#{:02X}{:02X}{:02X}'.format(r, g, b), sum(buckets.values()) / max(1, len(px))


def _region(img, box):
    W, H = img.size
    x0, y0, x1, y1 = (int(box[0] * W), int(box[1] * H), int(box[2] * W), int(box[3] * H))
    return list(img.crop((x0, y0, max(x0 + 1, x1), max(y0 + 1, y1))).getdata())


def _band(img, y0, y1):
    """Fraction of 'inked' pixels in a full-width horizontal band, and its mean colour."""
    px = _region(img, (0.02, y0, 0.98, y1))
    inked = [p for p in px if _is_inked(*p)]
    if not px:
        return 0, (255, 255, 255)
    mean = tuple(sum(c[i] for c in inked) / len(inked) for i in range(3)) if inked else (255, 255, 255)
    return len(inked) / len(px), mean


def analyze(data, content_type='', filename=''):
    """Return a suggestion dict: name, desc, base, accent, preview_png (bytes), signals."""
    if not data:
        raise AnalyzeError("The file is empty.")
    if len(data) > MAX_BYTES:
        raise AnalyzeError("File too large — keep it under 8 MB.")

    img, text = _load(data, content_type, filename)
    W, H = img.size
    small = img.copy()
    small.thumbnail((240, 340))
    px = list(small.getdata())

    accent, color_ratio = _accent(px)

    # Find the densest band in the top 22% — invoices often have a little
    # white margin above a header bar.
    best = (0, (255, 255, 255))
    for y in range(0, 20, 2):
        frac, mean = _band(small, y / 100, (y + 8) / 100)
        if frac > best[0]:
            best = (frac, mean)
    top_frac, top_mean = best
    left_px = _region(small, (0.0, 0.25, 0.26, 0.85))
    left_frac = sum(1 for p in left_px if _is_colored(*p)[0]) / max(1, len(left_px))

    words = (text or '').lower()
    signals = []
    if re.search(r'\b(payroll|payslip|pay slip|salary|net pay)\b', words):
        base = 'payroll'; signals.append('mentions payroll / salary')
    elif re.search(r'\b(staffing|employee|working days|on-?boarded)\b', words):
        base = 'staffing'; signals.append('mentions staffing / employees')
    elif left_frac > 0.5:
        base = 'sidebar'; signals.append('coloured left column')
    elif top_frac > 0.6:
        luma = 0.299 * top_mean[0] + 0.587 * top_mean[1] + 0.114 * top_mean[2]
        base = 'modern' if luma < 70 else 'bold'
        signals.append('dark header band' if base == 'modern' else 'coloured header band')
    elif color_ratio < 0.004:
        base = 'minimal'; signals.append('almost no colour')
    else:
        base = 'classic'; signals.append('light header with accent rules')

    if not accent:
        accent = '#0F172A'
        signals.append('black & white — using a dark accent')
    else:
        signals.append(f'brand colour {accent}')

    stem = os.path.splitext(os.path.basename(filename or 'Uploaded design'))[0]
    stem = re.sub(r'[_\-]+', ' ', stem).strip()
    stem = re.sub(r'\b(invoice|inv|bill|template|final|copy|draft|sample|smk|test|v\d+|\d+)\b', '', stem, flags=re.I)
    stem = re.sub(r'\s+', ' ', stem).strip()
    # A file named after an invoice number leaves nothing useful — name it
    # after what was detected instead.
    layout_names = {'modern': 'Dark Header', 'bold': 'Bold Header', 'sidebar': 'Sidebar',
                    'minimal': 'Minimal', 'payroll': 'Payroll', 'staffing': 'Staffing', 'classic': 'Classic'}
    name = (stem.title() if len(stem) >= 3 else f"{layout_names.get(base, 'Custom')} Design")[:60]

    # Page-1 preview for the card (kept modest: ~600px tall).
    pv = img.copy()
    pv.thumbnail((440, 620))
    buf = io.BytesIO()
    pv.save(buf, 'PNG', optimize=True)

    return {
        'name': name,
        'base': base,
        'accent': accent,
        'signals': signals,
        'preview_png': buf.getvalue(),
    }
