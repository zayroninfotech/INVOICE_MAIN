"""Exact-copy templates — no AI.

The superadmin uploads a PDF of an invoice design. `extract()` copies page 1
as it is: every filled box, rule, image and piece of text with its position,
size, weight and colour. The superadmin then clicks the sample values on that
copy and says which invoice field each one is (`FIELDS`). `render()` rebuilds
the page with a real invoice's data in those spots; the mapped item row is
repeated once per line item and everything below it moves down.

All positions are PDF points (1/72 in) relative to the page's top-left, which
is also what CSS `pt` means, so the HTML lines up with the source exactly.
"""
import html
import os

from django.conf import settings

# field key -> (label shown in the mapper, group). Keys starting with
# 'item.' belong to the repeating item row.
FIELDS = {
    'seller.name':    ('Your company name', 'Your business'),
    'seller.address': ('Your address', 'Your business'),
    'seller.email':   ('Your email', 'Your business'),
    'seller.phone':   ('Your phone', 'Your business'),
    'seller.website': ('Your website', 'Your business'),
    'seller.gst':     ('Your GSTIN', 'Your business'),
    'seller.pan':     ('Your PAN', 'Your business'),
    'seller.cin':     ('Your CIN', 'Your business'),
    'inv.number':     ('Invoice number', 'Invoice'),
    'inv.date':       ('Invoice date', 'Invoice'),
    'inv.due':        ('Due date', 'Invoice'),
    'cust.name':      ('Customer name', 'Customer'),
    'cust.recipient': ('Contact person', 'Customer'),
    'cust.address':   ('Customer address', 'Customer'),
    'cust.email':     ('Customer email', 'Customer'),
    'cust.phone':     ('Customer phone', 'Customer'),
    'cust.gst':       ('Customer GSTIN', 'Customer'),
    'cust.pan':       ('Customer PAN', 'Customer'),
    'cust.cin':       ('Customer CIN', 'Customer'),
    'item.sno':       ('Item — serial no.', 'Item row (repeats)'),
    'item.desc':      ('Item — description', 'Item row (repeats)'),
    'item.hsn':       ('Item — HSN/SAC', 'Item row (repeats)'),
    'item.qty':       ('Item — quantity', 'Item row (repeats)'),
    'item.unit':      ('Item — unit', 'Item row (repeats)'),
    'item.rate':      ('Item — rate', 'Item row (repeats)'),
    'item.disc':      ('Item — discount %', 'Item row (repeats)'),
    'item.amount':    ('Item — amount', 'Item row (repeats)'),
    'tot.subtotal':   ('Subtotal', 'Totals'),
    'tot.cgst':       ('CGST amount', 'Totals'),
    'tot.sgst':       ('SGST amount', 'Totals'),
    'tot.igst':       ('IGST amount', 'Totals'),
    'tot.cgst_lbl':   ('CGST label (e.g. "CGST @9%")', 'Totals'),
    'tot.sgst_lbl':   ('SGST label', 'Totals'),
    'tot.igst_lbl':   ('IGST label', 'Totals'),
    'tot.tax':        ('Total GST', 'Totals'),
    'tot.grand':      ('Grand total', 'Totals'),
    'tot.words':      ('Amount in words', 'Totals'),
    'note.notes':     ('Notes', 'Footer'),
    'note.terms':     ('Terms', 'Footer'),
    'sig.name':       ('Signatory name', 'Footer'),
    'sig.datetime':   ('Signed on (date/time)', 'Footer'),
}
# Right-aligned in the source → keep the right edge when the value changes length.
_RIGHT = {'item.qty', 'item.rate', 'item.disc', 'item.amount', 'tot.subtotal', 'tot.cgst',
          'tot.sgst', 'tot.igst', 'tot.tax', 'tot.grand'}
# Values that may run over several lines — keep the sample's box width and wrap.
_WRAP = {'seller.address', 'cust.address', 'note.notes', 'note.terms', 'tot.words', 'item.desc'}
IMAGE_ROLES = {'keep': 'Keep this image', 'logo': 'Your logo', 'signature': 'Signature', 'hide': 'Hide'}


class ExtractError(ValueError):
    pass


def _hex(c):
    """PyMuPDF colour → '#RRGGBB'. Accepts a 0-1 float tuple or a packed int."""
    if c is None:
        return None
    if isinstance(c, int):
        return '#{:06X}'.format(c & 0xFFFFFF)
    if len(c) == 1:
        c = (c[0], c[0], c[0])
    if len(c) == 4:   # CMYK → RGB
        cc, m, y, k = c
        c = ((1 - cc) * (1 - k), (1 - m) * (1 - k), (1 - y) * (1 - k))
    return '#{:02X}{:02X}{:02X}'.format(*(max(0, min(255, round(v * 255))) for v in c[:3]))


def extract(pdf_bytes, template_id):
    """Copy page 1 of a PDF into a layout dict. Images are saved under MEDIA_ROOT."""
    import fitz
    try:
        doc = fitz.open(stream=pdf_bytes, filetype='pdf')
    except Exception:
        raise ExtractError("Couldn't open that PDF. Export it again and re-upload.")
    if not len(doc):
        raise ExtractError("That PDF has no pages.")
    page = doc[0]
    W, H = page.rect.width, page.rect.height

    boxes = []
    for d in page.get_drawings():
        fill, stroke, width = _hex(d.get('fill')), _hex(d.get('color')), d.get('width') or 0
        op = d.get('fill_opacity', 1) if fill else 1
        for it in d.get('items', []):
            kind = it[0]
            if kind == 're':
                r = it[1]
                rect = [r.x0, r.y0, r.x1, r.y1]
            elif kind == 'qu':
                r = it[1].rect
                rect = [r.x0, r.y0, r.x1, r.y1]
            elif kind == 'l':
                p1, p2 = it[1], it[2]
                lw = max(width, 0.4)
                rect = [min(p1.x, p2.x), min(p1.y, p2.y), max(p1.x, p2.x), max(p1.y, p2.y)]
                if rect[2] - rect[0] < lw:
                    rect[2] = rect[0] + lw
                if rect[3] - rect[1] < lw:
                    rect[3] = rect[1] + lw
                boxes.append({'r': [round(v, 2) for v in rect], 'fill': stroke or '#000000', 'op': 1})
                continue
            else:
                continue
            if fill:
                boxes.append({'r': [round(v, 2) for v in rect], 'fill': fill, 'op': round(op, 2)})
            if stroke and width and d.get('type') in ('s', 'fs'):
                boxes.append({'r': [round(v, 2) for v in rect], 'stroke': stroke, 'sw': round(width, 2)})
    # Drop full-page white backgrounds — they would just hide nothing.
    boxes = [b for b in boxes if not (b.get('fill') in ('#FFFFFF',) and
                                      (b['r'][2] - b['r'][0]) > W * .95 and (b['r'][3] - b['r'][1]) > H * .95)]

    folder = os.path.join(settings.MEDIA_ROOT, 'template_assets', template_id)
    os.makedirs(folder, exist_ok=True)
    images = []
    for i, info in enumerate(page.get_image_info(xrefs=True)):
        xref = info.get('xref')
        if not xref:
            continue
        try:
            pix = fitz.Pixmap(doc, xref)
            if pix.n - pix.alpha > 3:
                pix = fitz.Pixmap(fitz.csRGB, pix)
            rel = f'template_assets/{template_id}/img{i}.png'
            pix.save(os.path.join(settings.MEDIA_ROOT, rel))
        except Exception:
            continue
        b = info['bbox']
        images.append({'id': f'i{i}', 'r': [round(v, 2) for v in (b[0], b[1], b[2], b[3])],
                       'src': rel, 'role': 'logo' if i == 0 and b[1] < H * .25 else 'keep'})

    spans = []
    n = 0
    for block in page.get_text('dict').get('blocks', []):
        for line in block.get('lines', []):
            for s in line.get('spans', []):
                text = s.get('text', '')
                if not text.strip():
                    continue
                font = (s.get('font') or '').lower()
                flags = s.get('flags', 0)
                spans.append({
                    'id': f's{n}', 't': text, 'r': [round(v, 2) for v in s['bbox']],
                    'size': round(s.get('size', 9), 2), 'color': _hex(s.get('color', 0)),
                    'bold': bool(flags & 16) or 'bold' in font or 'black' in font or 'heavy' in font,
                    'italic': bool(flags & 2) or 'italic' in font or 'oblique' in font,
                    'serif': any(k in font for k in ('times', 'serif', 'georgia', 'garamond', 'cambria'))
                             and 'sans' not in font,
                    'field': '',
                })
                n += 1
    if not spans:
        raise ExtractError("That PDF has no selectable text (it may be a scanned picture). "
                           "Export it from Word/Excel/your design tool as a PDF and try again.")
    return {'w': round(W, 2), 'h': round(H, 2), 'boxes': boxes, 'images': images, 'spans': spans}


# ── values ──────────────────────────────────────────────────────────────────

def values_from_context(ctx, invoice):
    """Field key → text for one invoice, using the same print context the
    built-in templates use (so formatting matches the rest of the app)."""
    strip = lambda s, p: (s or '').replace(p, '', 1).strip() if (s or '').startswith(p) else (s or '')
    v = {
        'seller.name': ctx.get('from_name') or ctx.get('co_name'), 'seller.address': ctx.get('from_addr'),
        'seller.email': ctx.get('from_email'), 'seller.phone': ctx.get('from_phone'),
        'seller.website': ctx.get('from_website'), 'seller.gst': strip(ctx.get('co_gst') or ctx.get('from_gst'), 'GSTIN: '),
        'seller.pan': strip(ctx.get('from_pan'), 'PAN: '), 'seller.cin': strip(ctx.get('from_cin'), 'CIN: '),
        'inv.number': ctx.get('inv_no'), 'inv.date': ctx.get('inv_date'), 'inv.due': ctx.get('bp_to'),
        'cust.name': ctx.get('to_name'), 'cust.recipient': strip(ctx.get('to_recipient'), 'Attn: '),
        'cust.address': ctx.get('to_addr'), 'cust.email': ctx.get('to_email'), 'cust.phone': ctx.get('to_phone'),
        'cust.gst': strip(ctx.get('to_gst'), 'GSTIN: '), 'cust.pan': strip(ctx.get('to_pan'), 'PAN: '),
        'cust.cin': strip(ctx.get('to_cin'), 'CIN: '),
        'tot.subtotal': ctx.get('sub'), 'tot.cgst': ctx.get('cgst'), 'tot.sgst': ctx.get('sgst'),
        'tot.igst': ctx.get('igst'), 'tot.cgst_lbl': ctx.get('cgst_lbl'), 'tot.sgst_lbl': ctx.get('sgst_lbl'),
        'tot.igst_lbl': ctx.get('igst_lbl'), 'tot.tax': ctx.get('gst_total'), 'tot.grand': ctx.get('grand'),
        'tot.words': ctx.get('words'), 'note.notes': ctx.get('notes'), 'note.terms': ctx.get('terms'),
        'sig.name': ctx.get('sig_name'), 'sig.datetime': strip(ctx.get('sig_dt'), 'Signed on '),
        '_logo': ctx.get('logo_url') or '', '_sig': ctx.get('sig_url') or '',
    }
    rs = lambda n: '₹{:,.2f}'.format(float(n or 0))
    items = []
    for i, it in enumerate(getattr(invoice, 'items', None) or [], 1):
        price, qty, disc = float(it.unit_price or 0), float(it.quantity or 0), float(it.discount or 0)
        items.append({
            'item.sno': str(i), 'item.desc': it.product_name or it.description or '',
            'item.hsn': it.hsn_code or '', 'item.qty': f'{qty:g}', 'item.unit': it.unit or '',
            'item.rate': rs(price), 'item.disc': f'{disc:g}%' if disc else '',
            'item.amount': rs(price * qty * (1 - disc / 100)),
        })
    v['_items'] = items
    for k in ('tot.subtotal', 'tot.cgst', 'tot.sgst', 'tot.igst', 'tot.tax', 'tot.grand'):
        if isinstance(v.get(k), (int, float)):
            v[k] = rs(v[k])
    return v


SAMPLE = {
    'seller.name': 'Your Company Pvt Ltd', 'seller.address': '12 MG Road, Bengaluru 560001',
    'seller.email': 'billing@yourcompany.in', 'seller.phone': '+91 98765 43210',
    'seller.website': 'yourcompany.in', 'seller.gst': '29ABCDE1234F1Z5', 'seller.pan': 'ABCDE1234F',
    'seller.cin': 'U12345KA2020PTC123456', 'inv.number': 'INV-2026-0001', 'inv.date': '02-10-2026',
    'inv.due': '01-11-2026', 'cust.name': 'Acme Traders', 'cust.recipient': 'R. Kumar',
    'cust.address': '45 Park Street, Kolkata 700016', 'cust.email': 'accounts@acme.in',
    'cust.phone': '+91 90000 11111', 'cust.gst': '19AAACA1111A1Z1', 'cust.pan': 'AAACA1111A',
    'cust.cin': 'L12345WB2001PLC000001', 'tot.subtotal': '₹37,500.00', 'tot.cgst': '₹3,375.00',
    'tot.sgst': '₹3,375.00', 'tot.igst': '₹0.00', 'tot.cgst_lbl': 'CGST @9%', 'tot.sgst_lbl': 'SGST @9%',
    'tot.igst_lbl': 'IGST @0%', 'tot.tax': '₹6,750.00', 'tot.grand': '₹44,250.00',
    'tot.words': 'Rupees Forty-Four Thousand Two Hundred Fifty Only', 'note.notes': 'Thank you for your business.',
    'note.terms': 'Payment due within 30 days.', 'sig.name': 'Authorised Signatory', 'sig.datetime': '02-10-2026, 11:30 AM',
    '_logo': '', '_sig': '',
    '_items': [
        {'item.sno': '1', 'item.desc': 'Website design', 'item.hsn': '998314', 'item.qty': '1', 'item.unit': 'Nos',
         'item.rate': '₹25,000.00', 'item.disc': '', 'item.amount': '₹25,000.00'},
        {'item.sno': '2', 'item.desc': 'Hosting (1 year)', 'item.hsn': '998315', 'item.qty': '1', 'item.unit': 'Nos',
         'item.rate': '₹12,500.00', 'item.disc': '', 'item.amount': '₹12,500.00'},
    ],
}


# ── render ──────────────────────────────────────────────────────────────────

def _media(src):
    if not src or src.startswith(('http', 'data:', '/', 'file:')):
        return src or ''
    return settings.MEDIA_URL + src


def render(layout, values, media=_media):
    """Return the page as HTML (an absolutely-positioned A4-style sheet in pt).

    `media` turns a MEDIA_ROOT-relative path into a URL the viewer can load —
    a /media/ URL in the browser, a file:/// path for the headless-Chrome PDF.
    """
    W, H = layout['w'], layout['h']
    spans = layout.get('spans', [])
    items = values.get('_items') or []

    # Item row band = vertical extent of the spans mapped to item fields.
    item_spans = [s for s in spans if (s.get('field') or '').startswith('item.')]
    if item_spans:
        y0 = min(s['r'][1] for s in item_spans)
        y1 = max(s['r'][3] for s in item_spans)
        pad = 3.0
        band = (y0 - pad / 2, y1 + pad / 2)
        row_h = band[1] - band[0]
        copies = max(len(items), 1)
        delta = row_h * (copies - 1) if items else 0
    else:
        band, row_h, copies, delta = None, 0, 1, 0

    def where(r):
        """'in' (inside the row band), 'below', 'across' (spans the band), or 'above'."""
        if not band:
            return 'above'
        top, bot = r[1], r[3]
        if top >= band[0] - .5 and bot <= band[1] + .5:
            return 'in'
        if top >= band[1] - .5:
            return 'below'
        if top < band[0] and bot > band[1]:
            return 'across'
        return 'above'

    out = []
    esc = html.escape
    bottom = [0.0]   # lowest drawn edge, so the sheet only grows when content really overflows

    def box(b, dy=0, dh=0):
        x0, y0_, x1, y1_ = b['r']
        st = (f"left:{x0:.2f}pt;top:{y0_ + dy:.2f}pt;width:{max(x1 - x0, .3):.2f}pt;"
              f"height:{max(y1_ - y0_ + dh, .3):.2f}pt;")
        if b.get('fill'):
            st += f"background:{b['fill']};" + (f"opacity:{b['op']};" if b.get('op', 1) < 1 else '')
        if b.get('stroke'):
            st += f"border:{b['sw']:.2f}pt solid {b['stroke']};box-sizing:border-box;"
        out.append(f'<div class="xb" style="{st}"></div>')
        bottom[0] = max(bottom[0], y1_ + dy + dh)

    for b in layout.get('boxes', []):
        pos = where(b['r'])
        if pos == 'in':
            for i in range(copies):
                box(b, dy=i * row_h)
        elif pos == 'below':
            box(b, dy=delta)
        elif pos == 'across':
            box(b, dh=delta)
        else:
            box(b)

    for im in layout.get('images', []):
        role = im.get('role', 'keep')
        if role == 'hide':
            continue
        src = {'logo': values.get('_logo'), 'signature': values.get('_sig')}.get(role) or (
            media(im['src']) if role == 'keep' else '')
        if not src:
            continue
        x0, y0_, x1, y1_ = im['r']
        dy = delta if where(im['r']) == 'below' else 0
        out.append(f'<img class="xi" src="{esc(src)}" alt="" style="left:{x0:.2f}pt;top:{y0_ + dy:.2f}pt;'
                   f'width:{x1 - x0:.2f}pt;height:{y1_ - y0_:.2f}pt;object-fit:contain">')
        bottom[0] = max(bottom[0], y1_ + dy)

    filled = [b['r'] for b in layout.get('boxes', []) if b.get('fill') and b['fill'] != '#FFFFFF']

    def container(r):
        """Smallest filled box that holds this text, if any."""
        best = None
        for b in filled:
            if b[0] - 1 <= r[0] and b[1] - 1 <= r[1] and r[2] <= b[2] + 1 and r[3] <= b[3] + 1:
                if best is None or (b[2] - b[0]) * (b[3] - b[1]) < (best[2] - best[0]) * (best[3] - best[1]):
                    best = b
        return best

    def text(s, value, dy=0):
        x0, y0_, x1, y1_ = s['r']
        f = s.get('field') or ''
        box_ = container(s['r']) if f in FIELDS else None
        st = (f"top:{y0_ + dy:.2f}pt;font-size:{s['size']:.2f}pt;color:{s.get('color') or '#000'};"
              f"font-weight:{700 if s.get('bold') else 400};font-style:{'italic' if s.get('italic') else 'normal'};"
              f"font-family:{'Times New Roman,Times,serif' if s.get('serif') else 'Helvetica,Arial,sans-serif'};")
        if f in _RIGHT:
            st += f"right:{W - x1:.2f}pt;text-align:right;white-space:nowrap;"
        elif box_:
            # A value inside a coloured block (e.g. company name in a header
            # box) wraps inside that block instead of spilling out of it.
            st += f"left:{x0:.2f}pt;width:{max(box_[2] - x0 - 4, 20):.2f}pt;white-space:normal;"
        elif f in _WRAP:
            # Room to wrap: at least the sample's width, up to most of the page.
            st += f"left:{x0:.2f}pt;width:{max(x1 - x0, min(W - x0 - 30, 320)):.2f}pt;white-space:normal;"
        else:
            st += f"left:{x0:.2f}pt;white-space:nowrap;"
        value = '' if value is None else str(value)
        out.append(f'<div class="xt" style="{st}">{esc(value)}</div>')
        if value:
            bottom[0] = max(bottom[0], y1_ + dy)

    for s in spans:
        f = s.get('field') or ''
        if f == 'hide':
            continue
        pos = where(s['r'])
        if f.startswith('item.'):
            for i, it in enumerate(items or [{}]):
                text(s, it.get(f, '') if items else '', dy=i * row_h)
            continue
        value = values.get(f, '') if f in FIELDS else s['t']
        # "GST: 36AAC…" mapped to a GSTIN field keeps its "GST: " label.
        if f in FIELDS and ':' in s['t']:
            label = s['t'].split(':', 1)[0]
            if 0 < len(label) <= 24:
                value = f'{label}: {value}' if value else ''
        if pos == 'in':
            # Static text inside the row (e.g. a currency sign) repeats with it.
            for i in range(copies):
                text(s, value, dy=i * row_h)
        else:
            text(s, value, dy=delta if pos == 'below' else 0)

    page_h = max(H, bottom[0] + 24)
    return (f'<div class="xt-page" style="position:relative;width:{W:.2f}pt;height:{page_h:.2f}pt;'
            f'background:#fff;overflow:hidden">'
            '<style>.xt-page .xb,.xt-page .xi,.xt-page .xt{position:absolute}'
            '.xt-page .xt{line-height:1.15;margin:0;padding:0}</style>'
            + ''.join(out) + '</div>')


def page_html(ctx, invoice, for_pdf=False):
    """Render ctx['exact_layout'] for one invoice. Returns '' for normal templates."""
    layout = ctx.get('exact_layout')
    if ctx.get('ai_page'):
        # AI-designed template (ai_template.py): render its HTML with this invoice's values.
        # (For the PDF, the caller already turned logo/signature into file:/// paths.)
        from .ai_template import render as ai_render, ai_context
        return ai_render(ctx['ai_page'], ai_context(values_from_context(ctx, invoice)))
    if not layout:
        return ''
    if for_pdf:
        media = lambda src: ('file:///' + os.path.join(settings.MEDIA_ROOT, src).replace(os.sep, '/')
                             if src and not src.startswith(('http', 'data:', 'file:')) else src)
    else:
        media = _media
    return render(layout, values_from_context(ctx, invoice), media=media)
