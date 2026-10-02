"""Read an uploaded invoice design and suggest a template for it.

Used by the superadmin "Add Invoice" page: upload a Word (.docx), PDF or image of an invoice
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
    raise AnalyzeError("Upload a Word (.docx), PDF or image (PNG, JPG, WEBP) of the invoice design.")


DOCX_TYPES = {'application/vnd.openxmlformats-officedocument.wordprocessingml.document'}


def _hex_rgb(v):
    v = (v or '').strip().lstrip('#')
    if not re.fullmatch(r'[0-9A-Fa-f]{6}', v):
        return None
    return tuple(int(v[i:i + 2], 16) for i in (0, 2, 4))


def _analyze_docx(data):
    """Read a Word (.docx) design from its XML — no rendering needed.

    Returns (colour_counts, header_rgb_or_None, sidebar_bool, text).
      colour_counts: Counter of RGB tuples, shading weighted above text colour
      header_rgb:    fill of a shaded block at the very top of the document
      sidebar:       a table whose first column is shaded on most rows
    """
    import zipfile
    import xml.etree.ElementTree as ET
    W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            xml = z.read('word/document.xml')
            heads = [z.read(n) for n in z.namelist() if re.match(r'word/header\d*\.xml$', n)]
    except Exception:
        raise AnalyzeError("Couldn't open that Word file. Save it as .docx (not the old .doc) or export it to PDF.")

    colours = Counter()
    texts = []

    def scan(root):
        for el in root.iter():
            tag = el.tag
            if tag == W + 'shd':
                rgb = _hex_rgb(el.get(W + 'fill'))
                if rgb:
                    colours[rgb] += 5          # a filled area matters more than coloured text
            elif tag == W + 'color':
                rgb = _hex_rgb(el.get(W + 'val'))
                if rgb:
                    colours[rgb] += 1
            elif tag == W + 't' and el.text:
                texts.append(el.text)

    def fill_of(el):
        shd = el.find(f'.//{W}shd')
        return _hex_rgb(shd.get(W + 'fill')) if shd is not None else None

    try:
        body = ET.fromstring(xml).find(W + 'body')
    except ET.ParseError:
        raise AnalyzeError("That Word file looks damaged. Try exporting it to PDF.")
    scan(body)
    for h in heads:
        try:
            scan(ET.fromstring(h))
        except ET.ParseError:
            pass

    # Header band: the first few blocks of the document carry a shading fill.
    header = None
    for el in list(body)[:4]:
        rgb = None
        if el.tag == W + 'tbl':
            first_row = el.find(W + 'tr')
            cells = first_row.findall(W + 'tc') if first_row is not None else []
            fills = [fill_of(c) for c in cells]
            filled = [f for f in fills if f and f != (255, 255, 255)]
            if cells and len(filled) >= max(1, len(cells) // 2):
                rgb = filled[0]
        elif el.tag == W + 'p':
            ppr = el.find(W + 'pPr')
            rgb = fill_of(ppr) if ppr is not None else None
        if rgb and rgb != (255, 255, 255):
            header = rgb
            break

    # Sidebar: some table shades its first column down its middle rows. The
    # header and total rows are ignored — an items table shades those anyway.
    sidebar = False
    for tbl in body.iter(W + 'tbl'):
        rows = tbl.findall(W + 'tr')[1:-1]
        if len(rows) < 3:
            continue
        shaded = 0
        for r in rows:
            first = r.find(W + 'tc')
            f = fill_of(first) if first is not None else None
            if f and f != (255, 255, 255):
                shaded += 1
        if shaded / len(rows) > 0.6 and len(rows[0].findall(W + 'tc')) >= 2:
            sidebar = True
            break

    return colours, header, sidebar, ' '.join(texts)


def _docx_preview(accent, base):
    """No renderer for Word on the server, so draw a small schematic of the
    detected layout in the detected colour for the card."""
    from PIL import ImageDraw
    acc = _hex_rgb(accent) or (193, 18, 31)
    img = Image.new('RGB', (440, 620), 'white')
    d = ImageDraw.Draw(img)
    grey = (226, 232, 240)
    if base == 'sidebar':
        d.rectangle([0, 0, 120, 620], fill=acc)
        x0 = 140
    else:
        x0 = 30
    if base in ('modern', 'bold'):
        d.rectangle([0, 0, 440, 110], fill=(15, 23, 42) if base == 'modern' else acc)
        d.rectangle([x0, 35, x0 + 150, 50], fill='white')
        d.rectangle([300, 35, 410, 62], fill=acc if base == 'modern' else 'white')
        y = 140
    else:
        d.rectangle([x0, 30, x0 + 150, 45], fill=(15, 23, 42))
        d.rectangle([300, 28, 410, 55], fill=acc)
        d.rectangle([x0, 75, 410, 79], fill=acc)
        y = 105
    for i in range(3):
        d.rectangle([x0, y + i * 18, x0 + 120, y + i * 18 + 7], fill=grey)
        d.rectangle([260, y + i * 18, 410, y + i * 18 + 7], fill=grey)
    y += 80
    d.rectangle([x0, y, 410, y + 22], fill=(15, 23, 42) if base != 'minimal' else grey)
    for i in range(5):
        yy = y + 40 + i * 26
        d.rectangle([x0, yy, x0 + 160, yy + 8], fill=grey)
        d.rectangle([340, yy, 410, yy + 8], fill=grey)
    d.rectangle([260, y + 190, 410, y + 216], fill=acc)
    return img


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

    name_l = (filename or '').lower()
    is_docx = content_type in DOCX_TYPES or name_l.endswith('.docx')
    if name_l.endswith('.doc') and not is_docx:
        raise AnalyzeError("Old .doc files can't be read. Open it in Word and save as .docx (or PDF), then upload again.")

    signals = []
    if is_docx:
        colours, header, sidebar, text = _analyze_docx(data)
        strong = Counter({rgb: n for rgb, n in colours.items() if _is_colored(*rgb)[0]})
        accent = '#{:02X}{:02X}{:02X}'.format(*strong.most_common(1)[0][0]) if strong else None
        top_frac, top_mean = (1.0, header) if header else (0.0, (255, 255, 255))
        left_frac = 1.0 if sidebar else 0.0
        color_ratio = 1.0 if strong else 0.0
        signals.append('read from Word file')
        img = None
    else:
        img, text = _load(data, content_type, filename)
        small = img.copy()
        small.thumbnail((240, 340))
        px = list(small.getdata())
        accent, color_ratio = _accent(px)
        # Densest band in the top 22% — invoices often have a little white
        # margin above a header bar.
        best = (0, (255, 255, 255))
        for y in range(0, 20, 2):
            frac, mean = _band(small, y / 100, (y + 8) / 100)
            if frac > best[0]:
                best = (frac, mean)
        top_frac, top_mean = best
        left_px = _region(small, (0.0, 0.25, 0.26, 0.85))
        left_frac = sum(1 for p in left_px if _is_colored(*p)[0]) / max(1, len(left_px))

    words = (text or '').lower()
    if re.search(r'\b(payroll|payslip|pay slip|salary|net pay)\b', words):
        base = 'payroll'; signals.append('mentions payroll / salary')
    elif re.search(r'\b(staffing|employees?|working days|on-?boarded)\b', words):
        base = 'staffing'; signals.append('mentions staffing / employees')
    elif left_frac > 0.5:
        base = 'sidebar'; signals.append('coloured left column')
    elif top_frac > 0.6:
        luma = 0.299 * top_mean[0] + 0.587 * top_mean[1] + 0.114 * top_mean[2]
        base = 'modern' if luma < 70 else 'bold'
        signals.append('dark header band' if base == 'modern' else 'coloured header band')
        if not accent and base == 'bold':
            accent = '#{:02X}{:02X}{:02X}'.format(*[int(c) for c in top_mean])
    elif color_ratio < 0.004:
        base = 'minimal'; signals.append('almost no colour')
    else:
        base = 'classic'; signals.append('light header with accent rules')

    if not accent:
        accent = '#0F172A'
        signals.append('black & white — using a dark accent')
    else:
        signals.append(f'brand colour {accent}')

    if img is None:
        img = _docx_preview(accent, base)

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
