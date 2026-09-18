import os
import io
import base64
import logging
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.lib import colors
from reportlab.platypus import (SimpleDocTemplate, Table, TableStyle,
                                Paragraph, Spacer, HRFlowable, KeepTogether, Image, Flowable)
from reportlab.lib.enums import TA_RIGHT, TA_CENTER, TA_LEFT
from django.conf import settings

from .template_registry import SECTIONS, resolve_template, normalize_layout_config, get_template_spec

logger = logging.getLogger(__name__)


# ── Fonts ──────────────────────────────────────────────────────────────────────
# ReportLab's built-in Type1 Helvetica has no U+20B9 (₹), so the PDF used to
# print "Rs." while the web preview showed ₹. DejaVu Sans (vendored under
# apps/invoices/fonts/, Bitstream Vera + public-domain license) has the glyph.
FONT, FONT_B, FONT_I, FONT_BI = 'Helvetica', 'Helvetica-Bold', 'Helvetica-Oblique', 'Helvetica-BoldOblique'
RUPEE = 'Rs.'

def _register_fonts():
    global FONT, FONT_B, FONT_I, FONT_BI, RUPEE
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    d = os.path.join(os.path.dirname(__file__), 'fonts')
    faces = [('Inv', 'DejaVuSans.ttf'), ('Inv-Bold', 'DejaVuSans-Bold.ttf'),
             ('Inv-Italic', 'DejaVuSans-Oblique.ttf'), ('Inv-BoldItalic', 'DejaVuSans-BoldOblique.ttf')]
    for name, fn in faces:
        pdfmetrics.registerFont(TTFont(name, os.path.join(d, fn)))
    pdfmetrics.registerFontFamily('Inv', normal='Inv', bold='Inv-Bold',
                                  italic='Inv-Italic', boldItalic='Inv-BoldItalic')
    FONT, FONT_B, FONT_I, FONT_BI = 'Inv', 'Inv-Bold', 'Inv-Italic', 'Inv-BoldItalic'
    RUPEE = '₹'

try:
    _register_fonts()
except Exception:  # ponytail: silent fallback to Helvetica/"Rs." if the .ttf is missing
    pass


# ── Shared palette ─────────────────────────────────────────────────────────────
DARK   = colors.HexColor('#0F172A')
MUTED  = colors.HexColor('#64748B')
BORDER = colors.HexColor('#E2E8F0')
ROW_ALT= colors.HexColor('#F8FAFC')
WHITE  = colors.white
BAL_BG = colors.HexColor('#1E293B')

STATUS_COLORS = {
    'Paid':      colors.HexColor('#15803D'),
    'Draft':     MUTED,
    'Sent':      MUTED,
    'Partial':   colors.HexColor('#D97706'),
    'Overdue':   colors.HexColor('#B91C1C'),
    'Cancelled': MUTED,
}


def _accent(invoice, spec):
    """User's custom color override, else the template's spec accent."""
    hex_val = getattr(invoice, 'template_color', None) or spec['accent']
    try:
        return colors.HexColor(hex_val)
    except Exception:
        return colors.HexColor(spec['accent'])


def _style(name, **kw):
    d = dict(fontName=FONT, fontSize=9, textColor=DARK, leading=13, spaceAfter=0)
    d.update(kw)
    return ParagraphStyle(name, **d)


def _group_indian(value):
    """Indian digit grouping: 1,06,200.00 (last 3 digits, then pairs).

    The web preview formats money with JS `toLocaleString('en-IN')`, so the
    PDF has to group the same way or the same invoice shows two different
    numbers on screen and on paper.
    """
    neg = value < 0
    whole, _, frac = f"{abs(float(value)):.2f}".partition('.')
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        whole = ','.join(parts + [tail])
    return f"{'-' if neg else ''}{whole}.{frac}"


def _fmt(amount, currency='INR'):
    if currency == 'INR':
        return f"{RUPEE}{_group_indian(amount)}"
    return f"{currency} {float(amount):,.2f}"


class _CircleLogoImage(Flowable):
    """Logo image clipped to a circle — matches the web preview's circular logo display."""
    def __init__(self, logo_path, d=1.05*cm):
        super().__init__()
        self.logo_path = logo_path
        self.d = d
        self.width = self.height = d

    def draw(self):
        from reportlab.lib.utils import ImageReader
        c = self.canv
        d = self.d
        r = d / 2
        c.saveState()
        p = c.beginPath()
        p.circle(r, r, r)
        c.clipPath(p, stroke=0, fill=0)
        try:
            ir = ImageReader(self.logo_path)
            c.drawImage(ir, 0, 0, width=d, height=d, preserveAspectRatio=True, anchor='c', mask='auto')
        except Exception:
            # Keep rendering the invoice, but leave a trace — a silent pass here
            # makes a bad logo file indistinguishable from no logo at all.
            logger.warning("logo draw failed for %s", self.logo_path, exc_info=True)
        c.restoreState()


def _logo_image(logo_path_str, max_w=3.5*cm, max_h=2.5*cm):
    if not logo_path_str:
        return None
    logo_abs = os.path.join(settings.MEDIA_ROOT, logo_path_str)
    if not os.path.exists(logo_abs):
        return None
    # Was `min(max_w, max_h, 1.05*cm)` — the fixed 1.05cm floor always won,
    # silently capping every logo to that size regardless of the caller's
    # logo_width setting (both here and the logged-in form's own slider).
    d = min(max_w, max_h)
    return _CircleLogoImage(logo_abs, d=d)


def _sig_image_flowable(src, max_w=130, max_h=42):
    """Decode a signature into a right-sized ReportLab Image, mirroring
    `_logo_image`'s decode-and-scale approach. Accepts either a base64 data-URL
    (the logged-in form posts `signature_image` that way) or a MEDIA_ROOT-relative
    path (the no-login generator saves the drawn/uploaded signature as a file)."""
    if not src:
        return None
    try:
        abs_path = os.path.join(settings.MEDIA_ROOT, src) if not src.startswith('data:') else ''
        if abs_path and os.path.exists(abs_path):
            with open(abs_path, 'rb') as fh:
                raw = fh.read()
        else:
            b64 = src.split(',', 1)[1] if ',' in src else src
            raw = base64.b64decode(b64)
        bio = io.BytesIO(raw)
        from PIL import Image as PILImage
        with PILImage.open(bio) as pil:
            pw, ph = pil.size
        scale = min(max_w / pw, max_h / ph) if pw and ph else 1
        bio.seek(0)
        return Image(bio, width=pw * scale, height=ph * scale)
    except Exception:
        return None


def _initials(name):
    """First letters of up to the first two words, matching form.html's
    live-preview logo-circle behaviour (e.g. `pvw-logo-circle` text)."""
    words = [w for w in (name or '').split() if w]
    if not words:
        return '?'
    if len(words) == 1:
        return words[0][:2].upper()
    return (words[0][0] + words[1][0]).upper()


class _CircleBadge(Flowable):
    """Filled circle with centered initials — the ReportLab equivalent of
    the web preview's `.pvw-circle` logo badge."""
    def __init__(self, initials, bg, fg=None, d=1.05*cm, font_size=11):
        super().__init__()
        self.initials = initials
        self.bg = bg
        self.fg = fg or colors.white
        self.d = d
        self.font_size = font_size
        self.width = self.height = d

    def draw(self):
        c = self.canv
        r = self.d / 2
        c.setFillColor(self.bg)
        c.circle(r, r, r, stroke=0, fill=1)
        c.setFillColor(self.fg)
        c.setFont(FONT_B, self.font_size)
        c.drawCentredString(r, r - self.font_size * 0.35, self.initials)


def _thank_you_stamp(ctx):
    """Small italic 'Thank you for your business!' line near the signature
    area — skipped for the 'minimal' template, matching the web preview's
    `.pvw-thanks` (and its `.inv-preview.tpl-minimal` hide rule). Previously a
    large circular 'Thank YOU!' badge; simplified to match the web preview
    after the badge was replaced there with plain text."""
    if ctx.get('style_id') == 'minimal':
        return []
    p = Paragraph('Thank you for your business!',
                  _style('ThanksLine', fontSize=8, fontName=FONT_I, textColor=MUTED))
    wrap = Table([[p]], colWidths=[ctx['CW']], hAlign='CENTER')
    wrap.setStyle(TableStyle([
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('TOPPADDING', (0, 0), (-1, -1), 6), ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
    ]))
    return [wrap]


def _addr_bar_block(ctx, bar_bg, bar_text=None, name_color=DARK, body_color=MUTED, border=BORDER):
    """Two-column FROM | BILL TO block with a solid-color label bar, matching
    the web preview's `.pvw-addr-hdr` (colored bar, white text) sitting above
    `.pvw-addr-body`. Used by templates whose header_style calls for a solid
    accent bar rather than a plain label + rule line."""
    bar_text = bar_text or WHITE
    from_lines, to_lines = _party_blocks(ctx)
    lbl_st = _style('ABL', fontSize=7.5, fontName=FONT_B, textColor=bar_text,
                    leading=10, alignment=TA_LEFT)
    nm_st  = _style('ABN', fontSize=9.5, fontName=FONT_B, textColor=name_color, leading=13)
    bd_st  = _style('ABB', fontSize=8.5, textColor=body_color, leading=12)

    def body(lines):
        out, first = [], True
        for ln in lines:
            if not ln:
                continue
            out.append(Paragraph(ln, nm_st if first else bd_st))
            first = False
        if first:
            out.append(Paragraph('—', bd_st))
        return out

    rows = [
        [Paragraph('FROM', lbl_st), Paragraph('BILL TO', lbl_st)],
        [body(from_lines), body(to_lines)],
    ]
    tbl = Table(rows, colWidths=[ctx['CW'] * 0.5, ctx['CW'] * 0.5])
    tbl.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), bar_bg),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 10), ('RIGHTPADDING', (0, 0), (-1, -1), 10),
        ('TOPPADDING', (0, 0), (-1, 0), 4), ('BOTTOMPADDING', (0, 0), (-1, 0), 4),
        ('TOPPADDING', (0, 1), (-1, 1), 8), ('BOTTOMPADDING', (0, 1), (-1, 1), 8),
        ('BOX', (0, 0), (-1, -1), 0.5, border),
        ('LINEAFTER', (0, 0), (0, -1), 0.5, border),
        ('LINEBELOW', (0, 0), (-1, 0), 0.5, border),
    ]))
    return tbl


def _seller_name_fallback(invoice):
    """Username of the invoice's owner — mirrors the web preview's
    `company_name || username || 'Your Company'` chain (form.html/detail.html)."""
    try:
        from apps.authentication.models import User
        u = User.objects(pk=getattr(invoice, 'created_by', None)).first()
        return (u.username if u else '') or ''
    except Exception:
        return ''


def _merge_seller(business_profile, seller, invoice=None):
    """Normalize both flows onto ONE sidecar-shaped dict.

    The no-login flow already builds this dict (free_views.py `seller_data`);
    the logged-in flow used to pass seller=None, so every consumer that reads
    ctx['seller'] — _party_blocks' CIN/PAN lines, _gst_split's real rates, the
    staffing footer — silently rendered blank for signed-in users. Synthesizing
    the same shape from BusinessProfile + Invoice fixes all of them at once
    instead of special-casing each template.

    `source` distinguishes the flows for the few places that genuinely differ.
    It defaults to 'sidecar' when read, so a hand-built dict (e.g. in
    test_pdf_fields.py) keeps its existing no-login behaviour.
    """
    if seller:
        return dict(seller, source=seller.get('source', 'sidecar'))
    bp = business_profile
    if not bp and invoice is None:
        return None
    inv = invoice
    out = {
        'source':   'profile',
        'name':     (bp.company_name if bp else '') or '',
        'email':    (bp.email if bp else '') or '',
        'phone':    (bp.phone if bp else '') or '',
        'gst':      (bp.gst if bp else '') or '',
        'cin':      (getattr(bp, 'cin', '') if bp else '') or '',
        'pan':      (getattr(bp, 'pan', '') if bp else '') or '',
        'website':  (bp.website if bp else '') or '',
        'address':  ', '.join(filter(None, [bp.address, bp.city,
                                            f"{bp.state} {bp.pincode}".strip()])) if bp else '',
        'logo_path': (bp.logo_path if bp else '') or '',
    }
    if inv is not None:
        out.update({
            'customer_phone': getattr(inv, 'customer_phone', '') or '',
            'customer_pan':   getattr(inv, 'customer_pan', '') or '',
            'customer_cin':   getattr(inv, 'customer_cin', '') or '',
            'recipient':      getattr(inv, 'customer_recipient', '') or '',
            'thankyou_msg':   getattr(inv, 'notes', '') or '',
            'department':     getattr(inv, 'department', '') or '',
            'sig_name':       getattr(inv, 'signatory_name', '') or '',
            'sig_company':    getattr(inv, 'signature_company', '') or '',
            'sig_path':       getattr(inv, 'signature_image', '') or '',
        })
        # Only advertise per-component rates when the invoice actually carries
        # them. Legacy invoices store 0, and setting the keys would flip
        # _gst_split onto the rate branch and wipe out their tax entirely.
        c, sg, i = (float(getattr(inv, 'cgst_rate', 0) or 0),
                    float(getattr(inv, 'sgst_rate', 0) or 0),
                    float(getattr(inv, 'igst_rate', 0) or 0))
        if c or sg or i:
            out.update({'cgst_rate': c, 'sgst_rate': sg, 'igst_rate': i})
    return out


def _seller_info(business_profile, seller, invoice=None):
    bp = business_profile
    s  = seller or {}
    name  = (bp.company_name if bp and bp.company_name else s.get('name', ''))
    if not name and invoice is not None:
        name = _seller_name_fallback(invoice) or 'Your Company'
    email = (bp.email        if bp and bp.email        else s.get('email', ''))
    phone = (bp.phone        if bp and bp.phone        else s.get('phone', ''))
    gst   = (bp.gst          if bp and bp.gst          else s.get('gst', ''))
    if bp:
        addr = ', '.join(filter(None, [bp.address, bp.city,
                                       f"{bp.state} {bp.pincode}".strip()]))
    else:
        addr = s.get('address', '')
    logo_path = (bp.logo_path if bp and bp.logo_path else s.get('logo_path', ''))
    return name, email, phone, gst, addr, logo_path


# ══════════════════════════════════════════════════════════════════════════════
# Shared section building blocks
# ══════════════════════════════════════════════════════════════════════════════

def _status_badge(status, sc_map, width=2.2*cm):
    sc = sc_map.get(status, MUTED)
    badge = Table([[Paragraph(status.upper(), _style('SB', fontSize=7.5, fontName=FONT_B,
                                                     textColor=WHITE, alignment=TA_CENTER))]],
                  colWidths=[width])
    badge.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), sc),
        ('TOPPADDING', (0, 0), (-1, -1), 3), ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ('LEFTPADDING', (0, 0), (-1, -1), 6), ('RIGHTPADDING', (0, 0), (-1, -1), 6),
    ]))
    return badge


def _items_rows(invoice, cur, cfg, CW, fs=9):
    """Build items table header + data rows honoring section_settings."""
    ss         = cfg.get('section_settings', {})
    show_hsn   = ss.get('items_table', {}).get('show_hsn', True)
    show_disc  = ss.get('items_table', {}).get('show_discount', True)

    # Money columns (RATE/AMOUNT) get generous widths: Indian digit grouping
    # ("1,06,200.00") plus the wider DejaVu metrics used to wrap mid-number.
    headers, col_w = [], []
    if show_hsn and show_disc:
        col_w  = [CW*0.28, CW*0.11, CW*0.17, CW*0.08, CW*0.09, CW*0.27]
        headers = ['DESCRIPTION', 'HSN/SAC', 'RATE', 'QTY', 'DISC%', 'AMOUNT']
    elif show_hsn:
        col_w  = [CW*0.32, CW*0.12, CW*0.19, CW*0.09, CW*0.28]
        headers = ['DESCRIPTION', 'HSN/SAC', 'RATE', 'QTY', 'AMOUNT']
    elif show_disc:
        col_w  = [CW*0.34, CW*0.19, CW*0.09, CW*0.10, CW*0.28]
        headers = ['DESCRIPTION', 'RATE', 'QTY', 'DISC%', 'AMOUNT']
    else:
        col_w  = [CW*0.40, CW*0.21, CW*0.10, CW*0.29]
        headers = ['DESCRIPTION', 'RATE', 'QTY', 'AMOUNT']

    th  = _style('TH',  fontSize=fs-1, fontName=FONT_B, textColor=MUTED, alignment=TA_LEFT)
    thr = _style('THR', fontSize=fs-1, fontName=FONT_B, textColor=MUTED, alignment=TA_RIGHT)
    header_row = [Paragraph(h, th if i == 0 else thr) for i, h in enumerate(headers)]
    rows = [header_row]

    for item in invoice.items:
        desc = item.product_name
        if getattr(item, 'description', '') and item.description != item.product_name:
            desc += f'\n<font size="{fs-1.5}" color="#94A3B8">{item.description}</font>'
        row = [Paragraph(desc, _style('D', fontSize=fs, textColor=DARK, leading=fs+4))]
        if show_hsn:
            row.append(Paragraph(getattr(item, 'hsn_code', '') or '',
                                 _style('H', fontSize=fs-1, textColor=MUTED, alignment=TA_RIGHT)))
        row.append(Paragraph(_fmt(item.unit_price, cur),
                             _style('R', fontSize=fs-0.5, textColor=DARK, alignment=TA_RIGHT,
                                    splitLongWords=0)))
        row.append(Paragraph(f"{float(item.quantity):g}",
                             _style('Q', fontSize=fs, textColor=DARK, alignment=TA_RIGHT)))
        if show_disc:
            row.append(Paragraph(f"{float(item.discount):g}%",
                                 _style('Dc', fontSize=fs, textColor=MUTED, alignment=TA_RIGHT)))
        row.append(Paragraph(_fmt(item.total, cur),
                             _style('A', fontSize=fs-0.5, fontName=FONT_B,
                                    textColor=DARK, alignment=TA_RIGHT, splitLongWords=0)))
        rows.append(row)

    return rows, col_w


def _items_section(ctx, hdr_bg=None, hdr_text=WHITE, hdr_rule=None, rule_color=BORDER,
                   pad=7, fs=9, zebra=True):
    """Generic items table section."""
    invoice, cur, cfg, CW, ACCENT = ctx['inv'], ctx['cur'], ctx['cfg'], ctx['CW'], ctx['ACCENT']
    rows, col_w = _items_rows(invoice, cur, cfg, CW, fs=fs)
    style = [
        ('LINEBELOW', (0, 1), (-1, -1), 0.4, rule_color),
        ('TOPPADDING', (0, 0), (-1, -1), pad), ('BOTTOMPADDING', (0, 0), (-1, -1), pad),
        ('LEFTPADDING', (0, 0), (-1, -1), 4), ('RIGHTPADDING', (0, 0), (-1, -1), 4),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ]
    if hdr_bg is not None:
        style += [('BACKGROUND', (0, 0), (-1, 0), hdr_bg), ('TEXTCOLOR', (0, 0), (-1, 0), hdr_text)]
    if hdr_rule is not None:
        style += [('LINEBELOW', (0, 0), (-1, 0), 1.5, hdr_rule)]
    elif hdr_bg is None:
        style += [('TEXTCOLOR', (0, 0), (-1, 0), DARK)]
    if zebra:
        style.append(('ROWBACKGROUNDS', (0, 1), (-1, -1), [WHITE, ROW_ALT]))
    tbl = Table(rows, colWidths=col_w, repeatRows=1)
    tbl.setStyle(TableStyle(style))
    return [tbl, Spacer(1, 10)] + _amount_words_block(ctx)


_ONES = ['', 'One', 'Two', 'Three', 'Four', 'Five', 'Six', 'Seven', 'Eight', 'Nine',
         'Ten', 'Eleven', 'Twelve', 'Thirteen', 'Fourteen', 'Fifteen', 'Sixteen',
         'Seventeen', 'Eighteen', 'Nineteen']
_TENS = ['', '', 'Twenty', 'Thirty', 'Forty', 'Fifty', 'Sixty', 'Seventy', 'Eighty', 'Ninety']


def _words_under_100(n):
    if n < 20:
        return _ONES[n]
    return _TENS[n // 10] + (' ' + _ONES[n % 10] if n % 10 else '')


def _words_int(n):
    """Indian numbering: hundred / thousand / lakh / crore."""
    if n == 0:
        return ''
    if n < 100:
        return _words_under_100(n)
    for div, unit in ((10000000, 'Crore'), (100000, 'Lakh'), (1000, 'Thousand'), (100, 'Hundred')):
        if n >= div:
            head = _words_int(n // div) if div > 100 else _ONES[n // div]
            rest = _words_int(n % div)
            return f"{head} {unit}" + (f" {rest}" if rest else '')
    return _words_under_100(n)


def _amount_words(amount):
    """'One Thousand Two Hundred Rupees and Fifty Paise Only.' — the Python twin
    of landing.html's amountToWords(), so the PDF band reads like the preview."""
    rupees = int(amount)
    paise = int(round((float(amount) - rupees) * 100))
    if paise == 100:            # 12.999 -> 13 rupees, 0 paise
        rupees, paise = rupees + 1, 0
    words = _words_int(rupees) or 'Zero'
    words += ' Rupee' + ('' if rupees == 1 else 's')
    if paise:
        words += ' and ' + _words_under_100(paise) + ' Paise'
    return words + ' Only.'


def _amount_words_block(ctx):
    """'Amount in Words' band between the items table and the totals — mirrors
    the no-login preview's `.pvw-words`. Only that flow shows it (the logged-in
    form's preview has no such band), so it is keyed off an explicit flag.

    NB: this used to test `ctx['seller']` truthiness as a proxy for "no-login
    flow". Now that the logged-in flow also gets a seller dict (_merge_seller),
    that proxy would light this band up on all nine other templates, so the
    check is on `source` instead."""
    s = ctx.get('seller') or {}
    if not s or s.get('source', 'sidecar') != 'sidecar':
        return []
    total = float(ctx['inv'].grand_total or 0)
    if total <= 0:
        return []
    amber_bg, amber_bd = colors.HexColor('#FFFBEB'), colors.HexColor('#FDE68A')
    lbl = Paragraph('AMOUNT IN WORDS', _style('AWL', fontSize=6.5, fontName=FONT_B,
                                              textColor=colors.HexColor('#B45309'), leading=9))
    txt = Paragraph(_amount_words(total), _style('AWT', fontSize=8, fontName=FONT_B,
                                                 textColor=colors.HexColor('#92400E'), leading=11))
    t = Table([[lbl], [txt]], colWidths=[ctx['CW']])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), amber_bg),
        ('BOX', (0, 0), (-1, -1), 0.5, amber_bd),
        ('LEFTPADDING', (0, 0), (-1, -1), 8), ('RIGHTPADDING', (0, 0), (-1, -1), 8),
        ('TOPPADDING', (0, 0), (0, 0), 5), ('BOTTOMPADDING', (0, 0), (0, 0), 0),
        ('TOPPADDING', (0, 1), (0, 1), 1), ('BOTTOMPADDING', (0, 1), (0, 1), 5),
    ]))
    return [t, Spacer(1, 6)]


def _gst_split(ctx):
    """Return (cgst_amt, sgst_amt, igst_amt, cgst_rate_lbl, sgst_rate_lbl, igst_rate_lbl).

    The no-login generator stores real rates/amounts in the seller sidecar — read
    those first.  Falls back to halving the combined tax_amount (IGST=0) with the
    rate from the first item, mirroring form.html's recalc()/updatePvw() JS."""
    invoice = ctx['inv']
    s = ctx.get('seller') or {}
    if s.get('cgst_rate') is not None or s.get('sgst_rate') is not None:
        c_rate = float(s.get('cgst_rate') or 0)
        s_rate = float(s.get('sgst_rate') or 0)
        i_rate = float(s.get('igst_rate') or 0)
        c_amt  = float(s.get('cgst_amt') or 0)
        s_amt  = float(s.get('sgst_amt') or 0)
        i_amt  = float(s.get('igst_amt') or 0)
        if not c_amt and not s_amt and not i_amt:
            base = float(invoice.subtotal or 0)
            c_amt = round(base * c_rate / 100, 2)
            s_amt = round(base * s_rate / 100, 2)
            i_amt = round(base * i_rate / 100, 2)
        return c_amt, s_amt, i_amt, f"{c_rate:g}", f"{s_rate:g}", f"{i_rate:g}"
    half_amt = round(float(invoice.tax_amount or 0) / 2, 2)
    items = list(invoice.items) if invoice.items else []
    rate = float(items[0].tax_rate or 0) / 2 if items else 0.0
    return half_amt, half_amt, 0.0, f"{rate:g}", f"{rate:g}", "0"


def _active_gst_rows(ctx):
    """(label, amount) pairs for only the GST components that apply.

    CGST+SGST (intra-state) and IGST (inter-state) are mutually exclusive, so
    a component with neither a rate nor an amount is dropped instead of being
    printed as an 'IGST @0%  ₹0.00' row."""
    cgst, sgst, igst, c_rate, s_rate, i_rate = _gst_split(ctx)
    rows = []
    for name, rate, amt in (('CGST', c_rate, cgst),
                            ('SGST', s_rate, sgst),
                            ('IGST', i_rate, igst)):
        if float(rate) > 0 or amt > 0:
            rows.append((f'{name} @{rate}%', amt))
    return rows


def _totals_stacked(ctx, tw_frac=0.38, bar_bg=None):
    """Classic right-aligned stacked totals box. GST split into CGST/SGST rows
    (matches the web preview's #pvw-cgst-lbl/#pvw-sgst-lbl breakdown) with a
    solid-color GRAND TOTAL bar."""
    invoice, cur, ACCENT = ctx['inv'], ctx['cur'], ctx['ACCENT']
    tw = ctx['CW'] * tw_frac
    bar_bg = bar_bg or ACCENT
    tot_lbl = _style('TL', fontSize=9, textColor=MUTED, alignment=TA_RIGHT)
    tot_val = _style('TV', fontSize=9, fontName=FONT_B, textColor=DARK, alignment=TA_RIGHT,
                     splitLongWords=0)
    bal_lbl = _style('BL', fontSize=10, fontName=FONT_B, textColor=WHITE, alignment=TA_RIGHT)
    bal_val = _style('BV', fontSize=10, fontName=FONT_B, textColor=WHITE, alignment=TA_RIGHT,
                     splitLongWords=0)
    data = [[Paragraph('Subtotal', tot_lbl), Paragraph(_fmt(invoice.subtotal, cur), tot_val)]]
    for lbl_txt, amt in _active_gst_rows(ctx):
        data.append([Paragraph(lbl_txt, tot_lbl), Paragraph(_fmt(amt, cur), tot_val)])
    data.append([Paragraph('GRAND TOTAL', bal_lbl), Paragraph(_fmt(invoice.grand_total, cur), bal_val)])
    last = len(data) - 1
    tbl = Table(data, colWidths=[tw*0.55, tw*0.45])
    tbl.setStyle(TableStyle([
        ('LEFTPADDING', (0, 0), (-1, -1), 6), ('RIGHTPADDING', (0, 0), (-1, -1), 6),
        ('TOPPADDING', (0, 0), (-1, -1), 5), ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ('LINEBELOW', (0, 0), (-1, last - 1), 0.5, BORDER),
        ('BACKGROUND', (0, last), (-1, last), bar_bg),
    ]))
    wrapper = Table([[tbl]], colWidths=[tw], hAlign='RIGHT')
    wrapper.setStyle(TableStyle([
        ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('TOPPADDING', (0, 0), (-1, -1), 0), ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
        ('BOX', (0, 0), (-1, -1), 0.5, BORDER),
    ]))
    return [wrapper]


def _totals_inline(ctx):
    """Compact single-row horizontal totals strip (data-dense layouts)."""
    invoice, cur, ACCENT = ctx['inv'], ctx['cur'], ctx['ACCENT']
    CW = ctx['CW']
    lbl = _style('CL', fontSize=7, fontName=FONT_B, textColor=MUTED, alignment=TA_CENTER, leading=9)
    val = _style('CV', fontSize=8.5, fontName=FONT_B, textColor=DARK, alignment=TA_CENTER, leading=11,
                 splitLongWords=0)
    bgl = _style('BL2', fontSize=7, fontName=FONT_B, textColor=WHITE, alignment=TA_CENTER, leading=9)
    bgv = _style('BV2', fontSize=9, fontName=FONT_B, textColor=WHITE, alignment=TA_CENTER, leading=12,
                 splitLongWords=0)
    gst_rows = _active_gst_rows(ctx)
    data = [
        [Paragraph('SUBTOTAL', lbl)] + [Paragraph(t, lbl) for t, _ in gst_rows] + [Paragraph('GRAND TOTAL', bgl)],
        [Paragraph(_fmt(invoice.subtotal, cur), val)] + [Paragraph(_fmt(a, cur), val) for _, a in gst_rows]
        + [Paragraph(_fmt(invoice.grand_total, cur), bgv)],
    ]
    last_col = len(gst_rows) + 1
    gst_w = CW * 0.54 / len(gst_rows) if gst_rows else 0
    col_widths = [CW*0.20] + [gst_w] * len(gst_rows) + [CW*0.26]
    tbl = Table(data, colWidths=col_widths)
    tbl.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (last_col - 1, -1), ROW_ALT),
        ('BACKGROUND', (last_col, 0), (last_col, -1), ACCENT),
        ('BOX', (0, 0), (-1, -1), 0.5, BORDER),
        ('INNERGRID', (0, 0), (-1, -1), 0.5, BORDER),
        ('TOPPADDING', (0, 0), (-1, -1), 3), ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ('LEFTPADDING', (0, 0), (-1, -1), 2), ('RIGHTPADDING', (0, 0), (-1, -1), 2),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ]))
    return [tbl]


def _notes_sec(ctx, label_color=DARK, body_color=MUTED, rule=True, fs=8.5):
    invoice, CW = ctx['inv'], ctx['CW']
    # The no-login generator posts notes='' and keeps its thank-you line in the
    # seller sidecar, so read that as the fallback body.
    body = invoice.notes or (ctx.get('seller') or {}).get('thankyou_msg', '')
    if not body:
        return []
    parts = []
    if rule:
        parts += [Spacer(1, 14), HRFlowable(width=CW, thickness=0.5, color=BORDER, spaceAfter=8)]
    else:
        parts += [Spacer(1, 10)]
    parts += [
        Paragraph('Notes', _style('NH', fontSize=fs, fontName=FONT_B, textColor=label_color, spaceAfter=3)),
        Paragraph(body, _style('NT', fontSize=fs, textColor=body_color, leading=fs+4.5)),
    ]
    return parts


def _terms_sec(ctx, label_color=DARK, body_color=MUTED, fs=8.5):
    invoice = ctx['inv']
    if not invoice.terms:
        return []
    return [
        Spacer(1, 6),
        Paragraph('Terms & Conditions', _style('TH2', fontSize=fs, fontName=FONT_B,
                                               textColor=label_color, spaceAfter=3)),
        Paragraph(invoice.terms, _style('NT2', fontSize=fs, textColor=body_color, leading=fs+4.5)),
    ]


def _signature_sec(ctx, label='Authorised Signatory', label_color=MUTED, line_color=None,
                   show_dept=True, stamp=True):
    """`show_dept`/`stamp` are opt-outs for templates whose own footer already
    prints the department and thank-you line (staffing), so they aren't
    repeated twice on the page."""
    CW = ctx['CW']
    inv = ctx['inv']
    lc = line_color or BORDER

    s = ctx.get('seller') or {}
    # The no-login generator stores these on the seller sidecar, not the Invoice.
    sig_image = getattr(inv, 'signature_image', '') or s.get('sig_path', '') or ''
    sig_name = getattr(inv, 'signatory_name', '') or s.get('sig_name', '') or ''
    sig_co = getattr(inv, 'signature_company', '') or s.get('sig_company', '') or ''
    dept = s.get('department', '') or ''
    img = _sig_image_flowable(sig_image)

    top_cell = img
    if top_cell is None and sig_name:
        top_cell = Paragraph(sig_name, _style('SIGT', fontSize=14, fontName=FONT_I,
                                               textColor=label_color, alignment=TA_CENTER))
    top_cell = top_cell or ''

    # Name printed below the line only when it's not already the drawn content itself.
    name_cell = Paragraph(sig_name, _style('SIGN2', fontSize=8, fontName=FONT_B,
                                           textColor=label_color, alignment=TA_CENTER)) if (sig_name and img) else ''

    # Centered signature column — matches `.pvw-sig-line{margin:14px auto}` /
    # `.pvw-sig-lbl{text-align:center}` in form.html's web preview (previously
    # right-aligned, which visually offset the image from the line/label below it).
    rows = [
        [top_cell],
        [Paragraph(label, _style('SIG', fontSize=7.5, textColor=label_color, alignment=TA_CENTER))],
        [name_cell],
    ]
    if sig_co:
        rows.append([Paragraph(sig_co, _style('SIGC', fontSize=7.5, textColor=MUTED,
                                              alignment=TA_CENTER))])
    if dept and show_dept:
        rows.append([Paragraph(dept, _style('SIGD', fontSize=7, textColor=MUTED,
                                            alignment=TA_CENTER))])
    sig_tbl = Table(rows, colWidths=[CW*0.4], hAlign='CENTER')
    sig_tbl.setStyle(TableStyle([
        ('LINEABOVE', (0, 1), (0, 1), 0.5, lc),
        ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('TOPPADDING', (0, 0), (-1, -1), 3), ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
    ]))
    return [Spacer(1, 12), sig_tbl] + (_thank_you_stamp(ctx) if stamp else [])


def _footer_center(text, color=MUTED, rule=False, CW=None, rule_color=None, rule_thick=0.5):
    parts = []
    if rule:
        parts += [Spacer(1, 10), HRFlowable(width=CW, thickness=rule_thick,
                                            color=rule_color or BORDER, spaceAfter=6)]
    else:
        parts += [Spacer(1, 16)]
    parts.append(Paragraph(text, _style('Ft', fontSize=7.5, textColor=color, alignment=TA_CENTER)))
    return parts


def _party_blocks(ctx, from_first=True):
    """Return (from_lines, to_lines) lists filtered by section settings."""
    invoice = ctx['inv']
    ss = ctx['cfg'].get('section_settings', {})
    show_header_gst = ss.get('header', {}).get('show_gstin', True)
    show_bill_gst   = ss.get('bill_to', {}).get('show_gst', True)
    # Free-generator users can rename a field's on-screen label (e.g. GSTIN ->
    # GST) via the pencil icon next to it; that same wording should carry
    # through here rather than only affecting the live preview.
    fl = ctx['cfg'].get('field_labels', {})

    s = ctx.get('seller') or {}

    from_lines = [ctx['s_name'], ctx['s_addr'], ctx['s_email'], ctx['s_phone']]
    # GST prints once: the header renders it when show_gstin is on, so the
    # FROM block only carries it as the fallback when that toggle is off.
    if ctx['s_gst'] and not show_header_gst:
        from_lines.append(f"{fl.get('fi-from-gst', 'GST')}: {ctx['s_gst']}")
    # Collected by the no-login generator and shown in its live preview.
    if s.get('cin'):
        from_lines.append(f"{fl.get('fi-from-cin', 'CIN')}: {s['cin']}")
    if s.get('pan'):
        from_lines.append(f"{fl.get('fi-from-pan', 'PAN')}: {s['pan']}")

    to_lines = [invoice.customer_name,
                getattr(invoice, 'customer_address', '') or '',
                invoice.customer_email]
    if s.get('customer_phone'):
        to_lines.append(s['customer_phone'])
    if invoice.customer_gst and show_bill_gst:
        to_lines.append(f"{fl.get('fi-cust-gst', 'GST')}: {invoice.customer_gst}")
    if s.get('customer_pan'):
        to_lines.append(f"{fl.get('fi-cust-pan', 'PAN')}: {s['customer_pan']}")
    return from_lines, to_lines


def _two_col_parties(ctx, lbl_color=MUTED, head_color=DARK, body_color=MUTED,
                     head_fs=10, body_fs=8.5):
    """Standard FROM | BILL TO two-column block."""
    from_lines, to_lines = _party_blocks(ctx)
    lbl_st  = _style('L2', fontSize=7.5, fontName=FONT_B, textColor=lbl_color, leading=10, spaceAfter=5)
    head_st = _style('FH', fontSize=head_fs, fontName=FONT_B, textColor=head_color, leading=head_fs+4)
    body_st = _style('FB', fontSize=body_fs, textColor=body_color, leading=body_fs+3.5)

    def blk(label, lines):
        b = [Paragraph(label, lbl_st)]
        first = True
        for ln in lines:
            if not ln:
                continue
            b.append(Paragraph(ln, head_st if first else body_st))
            first = False
        if first:
            b.append(Paragraph('—', body_st))
        return b

    ft = Table([[blk('FROM', from_lines), blk('BILL TO', to_lines)]],
               colWidths=[ctx['CW']*0.5, ctx['CW']*0.5])
    ft.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('TOPPADDING', (0, 0), (-1, -1), 0), ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
    ]))
    return ft


def _fmt_date(dt):
    return dt.strftime('%d %b %Y') if dt else '—'


# ══════════════════════════════════════════════════════════════════════════════
# Template 1: Classic — two-column header, ruled lines, zebra rows
# ══════════════════════════════════════════════════════════════════════════════

def _sec_classic(ctx):
    inv, CW, ACCENT = ctx['inv'], ctx['CW'], ctx['ACCENT']

    def header():
        badge = ctx['logo_img'] or _CircleBadge(_initials(ctx['s_name']), ACCENT)
        co_st = _style('CoN', fontSize=10, fontName=FONT_B, textColor=DARK, leading=13)
        gst_st = _style('CoG', fontSize=8, textColor=MUTED, leading=11)
        ss = ctx['cfg'].get('section_settings', {}).get('header', {})
        gstin_label = ctx['cfg'].get('field_labels', {}).get('fi-from-gst', 'GSTIN')
        co_right = [Paragraph(ctx['s_name'] or 'Your Company', co_st)]
        if ctx['s_gst'] and ss.get('show_gstin', True):
            co_right.append(Paragraph(f"{gstin_label}: {ctx['s_gst']}", gst_st))
        # A resized logo (via logo_width) needs a wider slot than the fixed
        # 1.3cm the small initials badge fits in, or it collides with the
        # company name/GSTIN text next to it.
        badge_col = max(1.3*cm, getattr(badge, 'width', 1.3*cm) + 0.4*cm)
        co_cell = Table([[badge, co_right]],
                        colWidths=[badge_col, CW*0.6 - badge_col])
        co_cell.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (0, 0), 8),
            ('TOPPADDING', (0, 0), (-1, -1), 0), ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
        ]))
        inv_label = Paragraph('INVOICE', _style('ILbl', fontSize=20, fontName=FONT_B,
                                                textColor=ACCENT, alignment=TA_RIGHT))
        hdr = Table([[co_cell, inv_label]], colWidths=[CW*0.6, CW*0.4])
        hdr.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'), ('ALIGN', (1, 0), (1, 0), 'RIGHT'),
            ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
        ]))
        return [hdr, Spacer(1, 10),
                HRFlowable(width=CW, thickness=3, color=ACCENT, spaceAfter=14)]

    def meta():
        fl = ctx['cfg'].get('field_labels', {})
        ml = _style('ML', fontSize=7.5, fontName=FONT_B, textColor=MUTED, leading=10, spaceAfter=3)
        mv = _style('MV', fontSize=9.5, fontName=FONT_B, textColor=DARK, leading=13)
        meta_t = Table(
            [[Paragraph(fl.get('fi-inv-number', 'NUMBER'), ml), Paragraph(fl.get('fi-inv-date', 'DATE'), ml)],
             [Paragraph(inv.invoice_number, mv), Paragraph(_fmt_date(inv.invoice_date), mv)]],
            colWidths=[CW*0.50, CW*0.50],
        )
        meta_t.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0),
            ('TOPPADDING', (0, 0), (-1, -1), 2), ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
        ]))
        return [meta_t, Spacer(1, 14)]

    def bill_to():
        ft = _addr_bar_block(ctx, ACCENT)
        return [ft, Spacer(1, 14)]

    def items():
        return _items_section(ctx, hdr_rule=ACCENT)

    def totals():
        return _totals_stacked(ctx, 0.38)

    def notes():
        return _notes_sec(ctx)

    def terms():
        return _terms_sec(ctx)

    def signature():
        return _signature_sec(ctx)

    def footer():
        return _footer_center(f"Computer-generated invoice | {inv.invoice_number}",
                              rule=True, CW=CW)

    return dict(header=header, invoice_meta=meta, bill_to=bill_to, items_table=items,
                totals=totals, notes=notes, terms=terms, signature=signature, footer=footer)


# ══════════════════════════════════════════════════════════════════════════════
# Template 2: Minimal — typography-first, hairline rules, no fills
# ══════════════════════════════════════════════════════════════════════════════

def _sec_minimal(ctx):
    inv, CW, ACCENT = ctx['inv'], ctx['CW'], ctx['ACCENT']
    NEAR_BLACK = colors.HexColor(ctx['spec']['secondary'])

    def header():
        co_st  = _style('CO', fontSize=13, fontName=FONT_B, textColor=NEAR_BLACK, leading=16)
        inv_st = _style('IN', fontSize=22, fontName=FONT_B, textColor=NEAR_BLACK,
                        leading=26, alignment=TA_RIGHT)
        num_st = _style('NU', fontSize=8.5, textColor=MUTED, alignment=TA_RIGHT)
        logo_or_name = ctx['logo_img'] if ctx['logo_img'] else Paragraph(ctx['s_name'] or '', co_st)
        right_cell = Table([[Paragraph('INVOICE', inv_st)], [Paragraph(inv.invoice_number, num_st)]],
                           colWidths=[CW*0.45])
        right_cell.setStyle(TableStyle([
            ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0),
            ('TOPPADDING', (0, 0), (-1, -1), 0), ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
        ]))
        hdr = Table([[logo_or_name, right_cell]], colWidths=[CW*0.55, CW*0.45])
        hdr.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0),
            ('TOPPADDING', (0, 0), (-1, -1), 0), ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
        ]))
        return [hdr, Spacer(1, 20),
                HRFlowable(width=CW, thickness=0.5, color=NEAR_BLACK, spaceAfter=14)]

    def meta():
        date_label = ctx['cfg'].get('field_labels', {}).get('fi-inv-date', 'DATE')
        sm = _style('SM2', fontSize=7.5, fontName=FONT_B, textColor=MUTED, leading=10, spaceAfter=2)
        bd = _style('BD2', fontSize=8.5, textColor=MUTED, leading=12)
        # Two label/value pairs as a row of columns, mirroring .pvw-meta.
        # 'DUE' has no counterpart in the free generator's form, so it isn't renameable.
        labels = [Paragraph(t, sm) for t in (date_label, 'DUE')]
        values = [Paragraph(_fmt_date(inv.invoice_date), bd),
                  Paragraph(_fmt_date(inv.due_date), bd)]
        w = CW / 2.0
        wrap = Table([labels, values], colWidths=[w, w])
        wrap.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 8),
            ('TOPPADDING', (0, 0), (-1, -1), 0), ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
        ]))
        return [wrap, Spacer(1, 14)]

    def bill_to():
        from_lines, to_lines = _party_blocks(ctx)
        sm  = _style('SM3', fontSize=7.5, fontName=FONT_B, textColor=MUTED, leading=10, spaceAfter=4)
        nm  = _style('NM', fontSize=9.5, fontName=FONT_B, textColor=NEAR_BLACK, leading=13)
        bdy = _style('BD', fontSize=8.5, textColor=MUTED, leading=12)

        def mini_block(label, lines):
            b = [Paragraph(label, sm)]
            first = True
            for ln in lines:
                if not ln:
                    continue
                b.append(Paragraph(ln, nm if first else bdy))
                first = False
            return b

        addr_tbl = Table([[mini_block('FROM', from_lines), mini_block('BILL TO', to_lines)]],
                         colWidths=[CW*0.5, CW*0.5])
        addr_tbl.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0),
            ('TOPPADDING', (0, 0), (-1, -1), 0), ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
        ]))
        return [addr_tbl, Spacer(1, 20), HRFlowable(width=CW, thickness=0.5, color=BORDER, spaceAfter=10)]

    def items():
        return _items_section(ctx, hdr_rule=None, rule_color=BORDER, zebra=False,
                              hdr_bg=None, pad=6)

    def totals():
        t = _totals_stacked(ctx, 0.36, bar_bg=NEAR_BLACK)
        return t

    def notes():
        return _notes_sec(ctx, rule=False)

    def terms():
        return _terms_sec(ctx)

    def signature():
        return _signature_sec(ctx, label_color=NEAR_BLACK, line_color=NEAR_BLACK)

    def footer():
        return _footer_center(inv.invoice_number, color=MUTED, rule=True, CW=CW)

    return dict(header=header, invoice_meta=meta, bill_to=bill_to, items_table=items,
                totals=totals, notes=notes, terms=terms, signature=signature, footer=footer)


# ══════════════════════════════════════════════════════════════════════════════
# Template 3: Modern — dark full-width masthead band
# ══════════════════════════════════════════════════════════════════════════════

def _sec_modern(ctx):
    inv, CW, ACCENT = ctx['inv'], ctx['CW'], ctx['ACCENT']
    HDR_BG  = colors.HexColor(ctx['spec']['secondary'])
    HDR_SUB = colors.HexColor(ctx['spec']['muted'])

    def header():
        co_w  = _style('CW', fontSize=14, fontName=FONT_B, textColor=WHITE, leading=18)
        sub_w = _style('SW', fontSize=8, textColor=HDR_SUB, leading=11)
        inv_w = _style('IW', fontSize=9, fontName=FONT_B, textColor=HDR_SUB,
                       leading=12, alignment=TA_RIGHT)
        num_w = _style('NW', fontSize=13, fontName=FONT_B, textColor=WHITE,
                       leading=16, alignment=TA_RIGHT, wordWrap='CJK')
        co_lines = [Paragraph(ctx['s_name'] or 'Your Company', co_w)]
        if ctx['s_addr']:
            co_lines.append(Paragraph(ctx['s_addr'], sub_w))
        if ctx['s_email'] or ctx['s_phone']:
            co_lines.append(Paragraph(' | '.join(filter(None, [ctx['s_email'], ctx['s_phone']])), sub_w))
        ss = ctx['cfg'].get('section_settings', {}).get('header', {})
        if ctx['s_gst'] and ss.get('show_gstin', True):
            co_lines.append(Paragraph(f"GST: {ctx['s_gst']}", sub_w))

        # hdr_inner sits inside band's cell, which has 16pt padding on each
        # side - size hdr_inner's columns to the padded content width, not
        # the full band width, or its right edge clips against the padding.
        HDR_PAD = 16
        inner_w = CW - 2 * HDR_PAD

        badge = ctx['logo_img'] or _CircleBadge(_initials(ctx['s_name']), ACCENT)
        left_cell = Table([[badge, co_lines]], colWidths=[1.3*cm, inner_w*0.6 - 1.3*cm])
        left_cell.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (0, -1), 8),
            ('TOPPADDING', (0, 0), (-1, -1), 0), ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
        ]))

        right_lines = [Paragraph('INVOICE', inv_w), Paragraph(inv.invoice_number, num_w)]
        right_cell = Table([[p] for p in right_lines], colWidths=[inner_w*0.4])
        right_cell.setStyle(TableStyle([
            ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0),
            ('TOPPADDING', (0, 0), (-1, -1), 2), ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
            ('ALIGN', (0, 0), (-1, -1), 'RIGHT'),
        ]))
        hdr_inner = Table([[left_cell, right_cell]], colWidths=[inner_w*0.6, inner_w*0.4])
        hdr_inner.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0),
            ('TOPPADDING', (0, 0), (-1, -1), 0), ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
        ]))
        band = Table([[hdr_inner]], colWidths=[CW])
        band.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), HDR_BG),
            ('LEFTPADDING', (0, 0), (-1, -1), 16), ('RIGHTPADDING', (0, 0), (-1, -1), 16),
            ('TOPPADDING', (0, 0), (-1, -1), 18), ('BOTTOMPADDING', (0, 0), (-1, -1), 18),
            ('LINEBELOW', (0, 0), (-1, -1), 3, ACCENT),
        ]))
        return [band, Spacer(1, 18)]

    def meta():
        ml = _style('ML', fontSize=7.5, fontName=FONT_B, textColor=MUTED, leading=10, spaceAfter=3)
        mv = _style('MV', fontSize=9.5, fontName=FONT_B, textColor=DARK, leading=13)
        meta_t = Table(
            [[Paragraph('DATE', ml), Paragraph('DUE DATE', ml)],
             [Paragraph(_fmt_date(inv.invoice_date), mv), Paragraph(_fmt_date(inv.due_date), mv)]],
            colWidths=[CW*0.5, CW*0.5],
        )
        meta_t.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0),
            ('TOPPADDING', (0, 0), (-1, -1), 2), ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
        ]))
        return [meta_t, Spacer(1, 14), HRFlowable(width=CW, thickness=0.5, color=BORDER, spaceAfter=14)]

    def bill_to():
        ft = _addr_bar_block(ctx, ACCENT)
        return [ft, Spacer(1, 18)]

    def items():
        return _items_section(ctx, hdr_bg=HDR_BG, hdr_text=WHITE)

    def totals():
        return _totals_stacked(ctx, 0.38)

    def notes():
        return _notes_sec(ctx)

    def terms():
        return _terms_sec(ctx)

    def signature():
        return _signature_sec(ctx)

    def footer():
        return _footer_center(inv.invoice_number)

    return dict(header=header, invoice_meta=meta, bill_to=bill_to, items_table=items,
                totals=totals, notes=notes, terms=terms, signature=signature, footer=footer)


# ══════════════════════════════════════════════════════════════════════════════
# Template 4: Professional — accent sidebar strip + warm tinted bands
# ══════════════════════════════════════════════════════════════════════════════

def _sec_professional(ctx):
    inv, CW, ACCENT = ctx['inv'], ctx['CW'], ctx['ACCENT']
    SIDEBAR_W = 0.35 * cm
    LIGHT_ACC = colors.HexColor(ctx['spec']['tint'])
    BORDER_ACC = colors.HexColor(ctx['spec']['border'])

    def header():
        co_st  = _style('CO', fontSize=13, fontName=FONT_B, textColor=DARK, leading=17)
        gst_st = _style('GS', fontSize=8, textColor=MUTED, leading=11)
        inv_st = _style('IS', fontSize=24, fontName=FONT_B, textColor=ACCENT,
                        leading=28, alignment=TA_RIGHT)
        num_st = _style('NS', fontSize=9, textColor=MUTED, alignment=TA_RIGHT)

        ss = ctx['cfg'].get('section_settings', {}).get('header', {})
        co_cell = [Paragraph(ctx['s_name'] or 'Your Company', co_st)]
        if ctx['s_gst'] and ss.get('show_gstin', True):
            co_cell.append(Paragraph(f"GST: {ctx['s_gst']}", gst_st))
        badge = ctx['logo_img'] or _CircleBadge(_initials(ctx['s_name']), ACCENT)
        co_cell = [badge, Spacer(1, 4)] + co_cell

        right_cell = Table([[Paragraph('INVOICE', inv_st)], [Paragraph(inv.invoice_number, num_st)]],
                           colWidths=[CW*0.42])
        right_cell.setStyle(TableStyle([
            ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0),
            ('TOPPADDING', (0, 0), (-1, -1), 0), ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
        ]))
        sidebar_cell = Paragraph('', _style('sb'))
        top_tbl = Table([[sidebar_cell, co_cell, right_cell]],
                        colWidths=[SIDEBAR_W, CW*0.55-SIDEBAR_W, CW*0.45])
        top_tbl.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (0, -1), ACCENT),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('LEFTPADDING', (0, 0), (0, -1), 0), ('RIGHTPADDING', (0, 0), (0, -1), 0),
            ('LEFTPADDING', (1, 0), (1, -1), 10), ('RIGHTPADDING', (1, 0), (1, -1), 0),
            ('LEFTPADDING', (2, 0), (2, -1), 0), ('RIGHTPADDING', (2, 0), (2, -1), 0),
            ('TOPPADDING', (0, 0), (-1, -1), 0), ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
        ]))
        return [top_tbl, Spacer(1, 16)]

    def meta():
        lbl = _style('LB', fontSize=7.5, fontName=FONT_B, textColor=MUTED, leading=10, spaceAfter=3)
        val = _style('VL', fontSize=9.5, fontName=FONT_B, textColor=DARK, leading=13)
        blk = [
            Paragraph('INVOICE NO.', lbl), Paragraph(inv.invoice_number, val), Spacer(1, 5),
            Paragraph('INVOICE DATE', lbl), Paragraph(_fmt_date(inv.invoice_date), val), Spacer(1, 5),
            Paragraph('DUE DATE', lbl), Paragraph(_fmt_date(inv.due_date), val),
        ]
        wrap = Table([[blk]], colWidths=[CW*0.34], hAlign='RIGHT')
        wrap.setStyle(TableStyle([
            ('LEFTPADDING', (0, 0), (-1, -1), 12), ('RIGHTPADDING', (0, 0), (-1, -1), 12),
            ('TOPPADDING', (0, 0), (-1, -1), 10), ('BOTTOMPADDING', (0, 0), (-1, -1), 10),
            ('BACKGROUND', (0, 0), (-1, -1), LIGHT_ACC),
            ('BOX', (0, 0), (-1, -1), 0.5, BORDER_ACC),
        ]))
        return [wrap, Spacer(1, 14)]

    def bill_to():
        lbl = _style('LB2', fontSize=7.5, fontName=FONT_B, textColor=ACCENT, leading=10, spaceAfter=3)
        val = _style('VL2', fontSize=9.5, fontName=FONT_B, textColor=DARK, leading=13)
        sm  = _style('SMB', fontSize=8.5, textColor=MUTED, leading=12)
        from_lines, to_lines = _party_blocks(ctx)

        def blk(label, lines):
            b = [Paragraph(label, lbl)]
            first = True
            for ln in lines:
                if not ln:
                    continue
                b.append(Paragraph(ln, val if first else sm))
                first = False
            return b

        band = Table([[blk('FROM', from_lines), blk('BILL TO', to_lines)]],
                     colWidths=[CW*0.5, CW*0.5])
        band.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), LIGHT_ACC),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('LEFTPADDING', (0, 0), (-1, -1), 12), ('RIGHTPADDING', (0, 0), (-1, -1), 12),
            ('TOPPADDING', (0, 0), (-1, -1), 12), ('BOTTOMPADDING', (0, 0), (-1, -1), 12),
            ('LINEAFTER', (0, 0), (0, -1), 0.5, BORDER_ACC),
            ('BOX', (0, 0), (-1, -1), 0.5, BORDER_ACC),
        ]))
        return [band, Spacer(1, 16)]

    def items():
        return _items_section(ctx, hdr_bg=LIGHT_ACC, hdr_text=DARK, hdr_rule=ACCENT)

    def totals():
        return _totals_stacked(ctx, 0.38)

    def notes():
        return _notes_sec(ctx)

    def terms():
        return _terms_sec(ctx)

    def signature():
        return _signature_sec(ctx, line_color=ACCENT)

    def footer():
        parts = [Spacer(1, 20), HRFlowable(width=CW, thickness=2, color=ACCENT, spaceAfter=6),
                 Paragraph(f"{ctx['s_name']} | {inv.invoice_number}",
                           _style('Ft', fontSize=7.5, textColor=MUTED, alignment=TA_CENTER))]
        return parts

    return dict(header=header, invoice_meta=meta, bill_to=bill_to, items_table=items,
                totals=totals, notes=notes, terms=terms, signature=signature, footer=footer)


# ══════════════════════════════════════════════════════════════════════════════
# Template 5: Bold — full-bleed color header, giant type, colored footer band
# ══════════════════════════════════════════════════════════════════════════════

def _sec_bold(ctx):
    inv, CW, ACCENT = ctx['inv'], ctx['CW'], ctx['ACCENT']
    DARK2 = colors.HexColor(ctx['spec']['secondary'])

    def header():
        co_w  = _style('CW', fontSize=16, fontName=FONT_B, textColor=WHITE, leading=20)
        sub_w = _style('SW', fontSize=8.5, textColor=colors.HexColor('#FFE4CC'), leading=12)
        inv_w = _style('IW', fontSize=32, fontName=FONT_B, textColor=WHITE,
                       leading=36, alignment=TA_RIGHT)
        num_w = _style('NW', fontSize=9, textColor=colors.HexColor('#FFE4CC'), alignment=TA_RIGHT)

        co_items = []
        badge = ctx['logo_img'] or _CircleBadge(_initials(ctx['s_name']), DARK2)
        co_items += [badge, Spacer(1, 8)]
        co_items.append(Paragraph(ctx['s_name'] or 'Your Company', co_w))
        if ctx['s_addr']:
            co_items.append(Paragraph(ctx['s_addr'], sub_w))
        ss = ctx['cfg'].get('section_settings', {}).get('header', {})
        if ctx['s_gst'] and ss.get('show_gstin', True):
            co_items.append(Paragraph(f"GST: {ctx['s_gst']}", sub_w))

        right_items = [Paragraph('INVOICE', inv_w), Paragraph(inv.invoice_number, num_w)]
        right_cell = Table([[p] for p in right_items], colWidths=[CW*0.45])
        right_cell.setStyle(TableStyle([
            ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0),
            ('TOPPADDING', (0, 0), (-1, -1), 2), ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
        ]))
        hdr = Table([[co_items, right_cell]], colWidths=[CW*0.55, CW*0.45])
        hdr.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), ACCENT),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('LEFTPADDING', (0, 0), (-1, -1), 18), ('RIGHTPADDING', (0, 0), (-1, -1), 18),
            ('TOPPADDING', (0, 0), (-1, -1), 22), ('BOTTOMPADDING', (0, 0), (-1, -1), 22),
        ]))
        return [hdr, Spacer(1, 0)]

    def meta():
        line = Table(
            [[Paragraph(f"Invoice Date: {_fmt_date(inv.invoice_date)}&nbsp;&nbsp;&nbsp;"
                        f"Due: {_fmt_date(inv.due_date)}",
                        _style('DW', fontSize=8.5, textColor=WHITE, alignment=TA_RIGHT))]],
            colWidths=[CW])
        line.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), DARK2),
            ('LEFTPADDING', (0, 0), (-1, -1), 14), ('RIGHTPADDING', (0, 0), (-1, -1), 14),
            ('TOPPADDING', (0, 0), (-1, -1), 6), ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ]))
        return [line, Spacer(1, 18)]

    def bill_to():
        ft = _addr_bar_block(ctx, ACCENT)
        return [ft, Spacer(1, 18)]

    def items():
        return _items_section(ctx, hdr_bg=ACCENT, hdr_text=WHITE, pad=9)

    def totals():
        return _totals_stacked(ctx, 0.40)

    def notes():
        return _notes_sec(ctx)

    def terms():
        return _terms_sec(ctx)

    def signature():
        return _signature_sec(ctx)

    def footer():
        foot = Table([[Paragraph(f"{ctx['s_name'] or ''} | {inv.invoice_number} | Computer generated",
                                 _style('Ft', fontSize=7.5, textColor=WHITE, alignment=TA_CENTER))]],
                     colWidths=[CW])
        foot.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), ACCENT),
            ('TOPPADDING', (0, 0), (-1, -1), 8), ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ]))
        return [Spacer(1, 24), foot]

    return dict(header=header, invoice_meta=meta, bill_to=bill_to, items_table=items,
                totals=totals, notes=notes, terms=terms, signature=signature, footer=footer)


# ══════════════════════════════════════════════════════════════════════════════
# Template 6: Compact — condensed, data-dense format
# ══════════════════════════════════════════════════════════════════════════════

def _sec_compact(ctx):
    inv, CW, ACCENT = ctx['inv'], ctx['CW'], ctx['ACCENT']

    def header():
        co_st = _style('CCO', fontSize=11, fontName=FONT_B, textColor=DARK, leading=14)
        sub_st = _style('CSU', fontSize=7, textColor=MUTED, leading=9)
        inv_st = _style('CIN', fontSize=15, fontName=FONT_B, textColor=ACCENT,
                        leading=18, alignment=TA_RIGHT)
        num_st = _style('CNU', fontSize=7.5, textColor=MUTED, alignment=TA_RIGHT)
        left_items = []
        badge = ctx['logo_img'] or _CircleBadge(_initials(ctx['s_name']), ACCENT, d=0.85*cm, font_size=9)
        left_items.append(badge)
        left_items.append(Spacer(1, 3))
        left_items.append(Paragraph(ctx['s_name'] or '', co_st))
        contact = ', '.join(filter(None, [ctx['s_addr'], ctx['s_email'], ctx['s_phone']]))
        ss = ctx['cfg'].get('section_settings', {}).get('header', {})
        if ctx['s_gst'] and ss.get('show_gstin', True):
            contact = f"GST: {ctx['s_gst']} | {contact}"
        if contact:
            left_items.append(Paragraph(contact[:180], sub_st))
        right_cell = Table([[Paragraph('INVOICE', inv_st)],
                            [Paragraph(inv.invoice_number, num_st)]], colWidths=[CW*0.32])
        right_cell.setStyle(TableStyle([
            ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0),
            ('TOPPADDING', (0, 0), (-1, -1), 0), ('BOTTOMPADDING', (0, 0), (-1, -1), 1),
        ]))
        hdr = Table([[left_items, right_cell]], colWidths=[CW*0.68, CW*0.32])
        hdr.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'TOP'), ('ALIGN', (1, 0), (1, 0), 'RIGHT'),
            ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0),
            ('TOPPADDING', (0, 0), (-1, -1), 0), ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
        ]))
        return [hdr, Spacer(1, 6), HRFlowable(width=CW, thickness=1, color=DARK, spaceAfter=6)]

    def meta():
        row = Table([[Paragraph(
            f"<b>No:</b> {inv.invoice_number} &nbsp;·&nbsp; <b>Date:</b> {_fmt_date(inv.invoice_date)}"
            f" &nbsp;·&nbsp; <b>Due:</b> {_fmt_date(inv.due_date)}",
            _style('CMV', fontSize=7.5, textColor=DARK))]], colWidths=[CW])
        row.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), ROW_ALT),
            ('BOX', (0, 0), (-1, -1), 0.4, BORDER),
            ('LEFTPADDING', (0, 0), (-1, -1), 6), ('RIGHTPADDING', (0, 0), (-1, -1), 6),
            ('TOPPADDING', (0, 0), (-1, -1), 3), ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ]))
        return [row, Spacer(1, 6)]

    def bill_to():
        ft = _addr_bar_block(ctx, ACCENT)
        return [ft, Spacer(1, 8)]

    def items():
        return _items_section(ctx, hdr_bg=DARK, hdr_text=WHITE, pad=3.5, fs=7.5, zebra=True)

    def totals():
        return _totals_inline(ctx)

    def notes():
        return _notes_sec(ctx, fs=7.5)

    def terms():
        return _terms_sec(ctx, fs=7.5)

    def signature():
        return _signature_sec(ctx)

    def footer():
        return [Spacer(1, 10), HRFlowable(width=CW, thickness=0.4, color=BORDER, spaceAfter=3),
                Paragraph(f"{inv.invoice_number} | Generated {inv.created_at.strftime('%d %b %Y') if inv.created_at else ''}",
                          _style('CF', fontSize=6.5, textColor=MUTED, alignment=TA_CENTER))]

    return dict(header=header, invoice_meta=meta, bill_to=bill_to, items_table=items,
                totals=totals, notes=notes, terms=terms, signature=signature, footer=footer)


# ══════════════════════════════════════════════════════════════════════════════
# Template 7: Sidebar — full-height identity panel on the left
# ══════════════════════════════════════════════════════════════════════════════

def _sec_sidebar(ctx):
    inv, CW, ACCENT = ctx['inv'], ctx['CW'], ctx['ACCENT']
    NAVY = colors.HexColor(ctx['spec']['secondary'])
    PANEL_W = CW * 0.32
    BODY_W  = CW - PANEL_W

    def header():
        panel_items = []
        badge = ctx['logo_img'] or _CircleBadge(_initials(ctx['s_name']), WHITE, fg=ACCENT)
        panel_items.append(badge)
        panel_items.append(Spacer(1, 10))
        panel_items.append(Paragraph(ctx['s_name'] or 'Your Company',
                                     _style('PN', fontSize=13, fontName=FONT_B,
                                            textColor=WHITE, leading=17)))
        panel_items.append(Spacer(1, 8))
        sub = _style('PS', fontSize=8, textColor=colors.HexColor('#C7D2E5'), leading=12)
        for ln in filter(None, [ctx['s_addr'], ctx['s_email'], ctx['s_phone']]):
            panel_items.append(Paragraph(ln, sub))
            panel_items.append(Spacer(1, 3))
        ss = ctx['cfg'].get('section_settings', {}).get('header', {})
        if ctx['s_gst'] and ss.get('show_gstin', True):
            panel_items.append(Spacer(1, 4))
            panel_items.append(Paragraph(f"GST: {ctx['s_gst']}", sub))

        right_items = [
            Paragraph('INVOICE', _style('SI', fontSize=30, fontName=FONT_B,
                                        textColor=NAVY, leading=34)),
            Spacer(1, 6),
            Paragraph(inv.invoice_number, _style('SN', fontSize=10, textColor=MUTED)),
        ]
        right_tbl = Table([[right_items]], colWidths=[BODY_W])
        right_tbl.setStyle(TableStyle([
            ('LEFTPADDING', (0, 0), (-1, -1), 18), ('RIGHTPADDING', (0, 0), (-1, -1), 0),
            ('TOPPADDING', (0, 0), (-1, -1), 4), ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ]))
        outer = Table([[panel_items, right_tbl]], colWidths=[PANEL_W, BODY_W])
        outer.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (0, -1), ACCENT),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('LEFTPADDING', (0, 0), (0, -1), 14), ('RIGHTPADDING', (0, 0), (0, -1), 14),
            ('TOPPADDING', (0, 0), (0, -1), 18), ('BOTTOMPADDING', (0, 0), (0, -1), 18),
            ('LEFTPADDING', (1, 0), (1, -1), 0), ('RIGHTPADDING', (1, 0), (1, -1), 0),
            ('TOPPADDING', (1, 0), (1, -1), 0), ('BOTTOMPADDING', (1, 0), (1, -1), 0),
        ]))
        return [outer, Spacer(1, 16)]

    def meta():
        lbl = _style('ML2', fontSize=7, fontName=FONT_B, textColor=MUTED, leading=9)
        val = _style('MV2', fontSize=9, fontName=FONT_B, textColor=NAVY, leading=12)
        rows = [[Paragraph('DATE', lbl), Paragraph(_fmt_date(inv.invoice_date), val)],
                [Paragraph('DUE DATE', lbl), Paragraph(_fmt_date(inv.due_date), val)],
                [Paragraph('NUMBER', lbl), Paragraph(inv.invoice_number, val)]]
        t = Table(rows, colWidths=[BODY_W*0.35, BODY_W*0.65], hAlign='RIGHT')
        t.setStyle(TableStyle([
            ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0),
            ('TOPPADDING', (0, 0), (-1, -1), 2), ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
            ('LINEABOVE', (0, 0), (-1, 0), 1, NAVY),
        ]))
        return [t, Spacer(1, 12)]

    def bill_to():
        _, to_lines = _party_blocks(ctx)
        lbl = _style('BT', fontSize=7.5, fontName=FONT_B, textColor=ACCENT, leading=10, spaceAfter=4)
        blk = [Paragraph('BILL TO', lbl)]
        first = True
        for ln in to_lines:
            if not ln:
                continue
            blk.append(Paragraph(ln,
                                 _style('BTN' if first else 'BTS',
                                        fontSize=10.5 if first else 8.5,
                                        fontName=FONT_B if first else FONT,
                                        textColor=NAVY if first else MUTED,
                                        leading=14 if first else 12)))
            first = False
        box = Table([[blk]], colWidths=[CW])
        box.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#F4F7FB')),
            ('LINEBEFORE', (0, 0), (0, -1), 3, NAVY),
            ('LEFTPADDING', (0, 0), (-1, -1), 12), ('RIGHTPADDING', (0, 0), (-1, -1), 12),
            ('TOPPADDING', (0, 0), (-1, -1), 10), ('BOTTOMPADDING', (0, 0), (-1, -1), 10),
        ]))
        return [box, Spacer(1, 14)]

    def items():
        return _items_section(ctx, hdr_bg=NAVY, hdr_text=WHITE, zebra=False)

    def totals():
        return _totals_stacked(ctx, 0.40, bar_bg=NAVY)

    def notes():
        return _notes_sec(ctx, label_color=NAVY)

    def terms():
        return _terms_sec(ctx, label_color=NAVY)

    def signature():
        return _signature_sec(ctx, label_color=NAVY, line_color=NAVY)

    def footer():
        return _footer_center(f"{ctx['s_name']} · {inv.invoice_number}", rule=True, CW=CW)

    return dict(header=header, invoice_meta=meta, bill_to=bill_to, items_table=items,
                totals=totals, notes=notes, terms=terms, signature=signature, footer=footer)


# ══════════════════════════════════════════════════════════════════════════════
# Template 8: Card — centered masthead, boxed card sections
# ══════════════════════════════════════════════════════════════════════════════

def _boxed(flowables, CW, bg=ROW_ALT, border=BORDER, pad=12, hAlign='LEFT'):
    inner = Table([[flowables]], colWidths=[CW], hAlign=hAlign)
    inner.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), bg),
        ('BOX', (0, 0), (-1, -1), 0.75, border),
        ('LEFTPADDING', (0, 0), (-1, -1), pad), ('RIGHTPADDING', (0, 0), (-1, -1), pad),
        ('TOPPADDING', (0, 0), (-1, -1), pad), ('BOTTOMPADDING', (0, 0), (-1, -1), pad),
    ]))
    return inner


def _sec_boxed(ctx):
    inv, CW, ACCENT = ctx['inv'], ctx['CW'], ctx['ACCENT']
    MINT = colors.HexColor(ctx['spec']['tint'])
    GREEN_DK = colors.HexColor(ctx['spec']['secondary'])
    MINT_BORDER = colors.HexColor(ctx['spec']['border'])

    def header():
        items = []
        badge = ctx['logo_img'] or _CircleBadge(_initials(ctx['s_name']), GREEN_DK, d=1.15*cm, font_size=12)
        lt = Table([[badge]], colWidths=[CW], hAlign='CENTER')
        lt.setStyle(TableStyle([('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                                ('LEFTPADDING', (0, 0), (-1, -1), 0),
                                ('RIGHTPADDING', (0, 0), (-1, -1), 0),
                                ('TOPPADDING', (0, 0), (-1, -1), 0),
                                ('BOTTOMPADDING', (0, 0), (-1, -1), 0)]))
        items += [lt, Spacer(1, 8)]
        ss = ctx['cfg'].get('section_settings', {}).get('header', {})
        items.append(Paragraph(ctx['s_name'] or 'Your Company',
                               _style('BH', fontSize=16, fontName=FONT_B,
                                      textColor=DARK, leading=20, alignment=TA_CENTER)))
        contact = ' · '.join(filter(None, [ctx['s_addr'], ctx['s_email'], ctx['s_phone']]))
        if ctx['s_gst'] and ss.get('show_gstin', True):
            contact = f"GST: {ctx['s_gst']} · {contact}"
        if contact:
            items.append(Spacer(1, 3))
            items.append(Paragraph(contact[:220],
                                   _style('BC', fontSize=8, textColor=MUTED, leading=11,
                                          alignment=TA_CENTER)))
        items.append(Spacer(1, 10))
        items.append(Paragraph('INVOICE', _style('BI', fontSize=13, fontName=FONT_B,
                                                 textColor=GREEN_DK, leading=16,
                                                 alignment=TA_CENTER)))
        return [_boxed(items, CW, bg=MINT, border=MINT_BORDER), Spacer(1, 12)]

    def meta():
        cells = [
            [Paragraph('NO.', _style('BM', fontSize=6.5, fontName=FONT_B,
                                     textColor=MUTED, alignment=TA_CENTER, leading=8)),
             Paragraph('DATE', _style('BM2', fontSize=6.5, fontName=FONT_B,
                                      textColor=MUTED, alignment=TA_CENTER, leading=8)),
             Paragraph('DUE', _style('BM3', fontSize=6.5, fontName=FONT_B,
                                     textColor=MUTED, alignment=TA_CENTER, leading=8))],
            [Paragraph(inv.invoice_number, _style('BV1', fontSize=8, fontName=FONT_B,
                                                  textColor=DARK, alignment=TA_CENTER, leading=10)),
             Paragraph(_fmt_date(inv.invoice_date), _style('BV2', fontSize=8, textColor=DARK,
                                                           alignment=TA_CENTER, leading=10)),
             Paragraph(_fmt_date(inv.due_date), _style('BV3', fontSize=8, textColor=DARK,
                                                       alignment=TA_CENTER, leading=10))],
        ]
        t = Table(cells, colWidths=[CW*0.34, CW*0.33, CW*0.33])
        t.setStyle(TableStyle([
            ('BOX', (0, 0), (-1, -1), 0.5, BORDER),
            ('INNERGRID', (0, 0), (-1, -1), 0.25, BORDER),
            ('BACKGROUND', (0, 0), (-1, 0), ROW_ALT),
            ('TOPPADDING', (0, 0), (-1, -1), 3), ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ]))
        return [t, Spacer(1, 12)]

    def bill_to():
        from_lines, to_lines = _party_blocks(ctx)
        lbl_st = _style('PL', fontSize=6.5, fontName=FONT_B, textColor=WHITE,
                        leading=8, alignment=TA_CENTER)
        nm_st  = _style('PN2', fontSize=9, fontName=FONT_B, textColor=DARK, leading=12)
        bd_st  = _style('PB', fontSize=8, textColor=MUTED, leading=11)

        def bar(label):
            t = Table([[Paragraph(label, lbl_st)]], colWidths=[CW*0.485 - 20])
            t.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, -1), GREEN_DK),
                ('TOPPADDING', (0, 0), (-1, -1), 3), ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
                ('LEFTPADDING', (0, 0), (-1, -1), 6), ('RIGHTPADDING', (0, 0), (-1, -1), 6),
            ]))
            return t

        def blk(label, lines):
            b = [bar(label), Spacer(1, 6)]
            first = True
            for ln in lines:
                if not ln:
                    continue
                b.append(Paragraph(ln, nm_st if first else bd_st))
                first = False
            return b

        left  = _boxed(blk('FROM', from_lines), CW*0.485, bg=WHITE, pad=10)
        right = _boxed(blk('BILL TO', to_lines), CW*0.485, bg=WHITE, pad=10)
        both = Table([[left, '', right]], colWidths=[CW*0.485, CW*0.03, CW*0.485])
        both.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0),
            ('TOPPADDING', (0, 0), (-1, -1), 0), ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
        ]))
        return [both, Spacer(1, 12)]

    def items():
        return _items_section(ctx, hdr_bg=ROW_ALT, hdr_text=DARK,
                              hdr_rule=GREEN_DK, rule_color=BORDER, pad=6)

    def totals():
        t = _totals_stacked(ctx, 0.42, bar_bg=GREEN_DK)[0]
        return [_boxed([t], CW*0.48, bg=WHITE, pad=8, hAlign='RIGHT')]

    def notes():
        sec = _notes_sec(ctx, label_color=GREEN_DK, rule=False)
        if not sec:
            return []
        return [_boxed(sec, CW, bg=WHITE), Spacer(1, 10)]

    def terms():
        sec = _terms_sec(ctx, label_color=GREEN_DK)
        if not sec:
            return []
        return [_boxed(sec, CW, bg=WHITE)]

    def signature():
        return _signature_sec(ctx, label_color=GREEN_DK)

    def footer():
        return _footer_center(f"{ctx['s_name']} · {inv.invoice_number} · Thank you for your business",
                              rule=True, CW=CW)

    return dict(header=header, invoice_meta=meta, bill_to=bill_to, items_table=items,
                totals=totals, notes=notes, terms=terms, signature=signature, footer=footer)


# ══════════════════════════════════════════════════════════════════════════════
# Template 9: Statement — creative editorial, oversized type, asymmetric blocks
# ══════════════════════════════════════════════════════════════════════════════

def _sec_statement(ctx):
    inv, CW, ACCENT = ctx['inv'], ctx['CW'], ctx['ACCENT']

    def header():
        left_items = []
        badge = ctx['logo_img'] or _CircleBadge(_initials(ctx['s_name']), ACCENT)
        left_items.append(badge)
        left_items.append(Spacer(1, 10))
        left_items.append(Paragraph('INVOICE', _style('GI', fontSize=42, fontName=FONT_B,
                                                      textColor=DARK, leading=46)))
        ss = ctx['cfg'].get('section_settings', {}).get('header', {})
        sub = _style('GS', fontSize=8.5, textColor=MUTED, leading=12)
        left_items.append(Spacer(1, 4))
        left_items.append(Paragraph((ctx['s_name'] or '').upper(), 
                                    _style('GN', fontSize=10, fontName=FONT_B,
                                           textColor=ACCENT, leading=13)))
        contact = ' · '.join(filter(None, [ctx['s_email'], ctx['s_phone']]))
        if ctx['s_gst'] and ss.get('show_gstin', True):
            contact = f"GST {ctx['s_gst']} · {contact}"
        if contact:
            left_items.append(Paragraph(contact, sub))

        # Oversized balance block, top-right
        bal_items = [
            Paragraph('AMOUNT DUE', _style('GA', fontSize=7, fontName=FONT_B,
                                           textColor=WHITE, alignment=TA_CENTER, leading=9)),
            Spacer(1, 4),
            Paragraph(_fmt(inv.grand_total, inv.currency),
                      _style('GB', fontSize=15, fontName=FONT_B, textColor=WHITE,
                             alignment=TA_CENTER, leading=19, splitLongWords=0)),
            Spacer(1, 4),
            Paragraph(f"DUE {_fmt_date(inv.due_date)}", _style('GD', fontSize=7,
                                                               textColor=colors.HexColor('#FFE4CC'),
                                                               alignment=TA_CENTER, leading=9)),
        ]
        bal_box = Table([[bal_items]], colWidths=[CW*0.34])
        bal_box.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), ACCENT),
            ('TOPPADDING', (0, 0), (-1, -1), 14), ('BOTTOMPADDING', (0, 0), (-1, -1), 14),
            ('LEFTPADDING', (0, 0), (-1, -1), 8), ('RIGHTPADDING', (0, 0), (-1, -1), 8),
        ]))
        outer = Table([[left_items, bal_box]], colWidths=[CW*0.66, CW*0.34])
        outer.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'TOP'), ('ALIGN', (1, 0), (1, 0), 'RIGHT'),
            ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0),
            ('TOPPADDING', (0, 0), (-1, -1), 0), ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
        ]))
        return [outer, Spacer(1, 14), HRFlowable(width=CW, thickness=2.5, color=DARK, spaceAfter=12)]

    def meta():
        rows = [
            [Paragraph('INVOICE NO.', _style('GM', fontSize=7, fontName=FONT_B,
                                             textColor=MUTED, leading=9, alignment=TA_RIGHT)),
             Paragraph(inv.invoice_number, _style('GMV', fontSize=9, fontName=FONT_B,
                                                  textColor=DARK, leading=12, alignment=TA_RIGHT))],
            [Paragraph('ISSUED', _style('GM2', fontSize=7, fontName=FONT_B,
                                        textColor=MUTED, leading=9, alignment=TA_RIGHT)),
             Paragraph(_fmt_date(inv.invoice_date), _style('GMV2', fontSize=9, textColor=DARK,
                                                           leading=12, alignment=TA_RIGHT))],
        ]
        t = Table(rows, colWidths=[CW*0.25, CW*0.35], hAlign='RIGHT')
        t.setStyle(TableStyle([
            ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0),
            ('TOPPADDING', (0, 0), (-1, -1), 2), ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ]))
        return [t, Spacer(1, 10)]

    def bill_to():
        _, to_lines = _party_blocks(ctx)
        blk = [Paragraph('BILLED TO', _style('GT', fontSize=7, fontName=FONT_B,
                                             textColor=ACCENT, leading=9, spaceAfter=5))]
        first = True
        for ln in to_lines:
            if not ln:
                continue
            blk.append(Paragraph(ln if first else ln,
                                 _style('GTN' if first else 'GTS',
                                        fontSize=14 if first else 8.5,
                                        fontName=FONT_B if first else FONT,
                                        textColor=DARK if first else MUTED,
                                        leading=18 if first else 12)))
            first = False
        return [*blk, Spacer(1, 16)]

    def items():
        return _items_section(ctx, hdr_bg=None, hdr_rule=DARK, rule_color=DARK,
                              pad=8, fs=9.5, zebra=False)

    def totals():
        invoice, cur = ctx['inv'], ctx['cur']
        small_l = _style('GTL', fontSize=8, textColor=MUTED, alignment=TA_RIGHT)
        small_v = _style('GTV', fontSize=8, fontName=FONT_B, textColor=DARK, alignment=TA_RIGHT,
                          splitLongWords=0)
        big_l = _style('GBL', fontSize=8, fontName=FONT_B, textColor=WHITE, alignment=TA_RIGHT)
        big_v = _style('GBV', fontSize=12, fontName=FONT_B, textColor=WHITE, alignment=TA_RIGHT,
                        splitLongWords=0)
        rows = [[Paragraph('Subtotal', small_l), Paragraph(_fmt(invoice.subtotal, cur), small_v)]]
        for lbl_txt, amt in _active_gst_rows(ctx):
            rows.append([Paragraph(lbl_txt, small_l), Paragraph(_fmt(amt, cur), small_v)])
        rows.append([Paragraph('GRAND TOTAL', big_l), Paragraph(_fmt(invoice.grand_total, cur), big_v)])
        t = Table(rows, colWidths=[ctx['CW']*0.20, ctx['CW']*0.26], hAlign='RIGHT')
        grand_idx = len(rows) - 1
        t.setStyle(TableStyle([
            ('LEFTPADDING', (0, 0), (-1, -1), 8), ('RIGHTPADDING', (0, 0), (-1, -1), 8),
            ('TOPPADDING', (0, 0), (-1, -1), 5), ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
            ('BACKGROUND', (0, grand_idx), (-1, grand_idx), DARK),
            ('LINEBELOW', (0, grand_idx - 1), (-1, grand_idx - 1), 0.5, BORDER),
        ]))
        return [t]

    def notes():
        return _notes_sec(ctx, rule=False, fs=9)

    def terms():
        return _terms_sec(ctx, fs=8)

    def signature():
        return _signature_sec(ctx, label_color=DARK, line_color=DARK)

    def footer():
        return [Spacer(1, 24), HRFlowable(width=ctx['CW'], thickness=0.5, color=BORDER, spaceAfter=4),
                Paragraph(f"{(ctx['s_name'] or '').upper()} — {inv.invoice_number}",
                          _style('GF', fontSize=6.5, textColor=MUTED, alignment=TA_CENTER))]

    return dict(header=header, invoice_meta=meta, bill_to=bill_to, items_table=items,
                totals=totals, notes=notes, terms=terms, signature=signature, footer=footer)


def _sec_staffing(ctx):
    """Staffing & payroll invoices — one document engine, two label sets.

    Items table uses HR-style columns: S.No | Date | Employee Name | Role |
    Working Days | Amount.  Field mapping:
        hsn_code    → Project On-Boarded Date / Pay Period
        product_name→ Employee Name
        description → Role / Designation
        quantity    → Working Days / Days Paid
        total       → Amount
    """
    inv, CW, ACCENT = ctx['inv'], ctx['CW'], ctx['ACCENT']
    STEEL   = colors.HexColor(ctx['spec']['tint'])       # table shading
    BLUE_LT = colors.HexColor(ctx['spec']['border'])     # rules
    # Verbatim from the reference document's XML.
    MAROON  = colors.HexColor('#901709')   # company name
    BODY    = colors.HexColor('#515151')   # body / labels
    HEAD    = colors.HexColor('#2D74B5')   # thank-you line
    RED     = colors.HexColor('#ED0000')   # department line
    TOT_LBL = colors.HexColor('#1E293A')   # "Total" row label
    s = ctx.get('seller') or {}

    # Payroll shares this builder with payroll-specific wording; staffing
    # keeps the reference document's verbatim labels.
    payroll = ctx.get('style_id') == 'payroll'
    LBL = {
        'date_col': 'Pay Period' if payroll else 'Project On-<br/>Boarded Date',
        'role_col': 'Designation' if payroll else 'Role',
        'days_col': 'Days<br/>Paid' if payroll else 'Working<br/>Days',
        'meta_id':  'Payroll ID' if payroll else 'Customer ID#',
        'period':   'Pay Period' if payroll else 'Billing Period',
        'grand':    'Net Pay' if payroll else 'Grand Total (Including GST)',
    }
    CO_COLOR = ACCENT if payroll else MAROON

    # Reference items grid: 700/1460/2514/2254/1080/1346 twips of 9354.
    COL_FR = [0.0748, 0.1561, 0.2688, 0.2410, 0.1155, 0.1439]
    COL_W  = [CW * f for f in COL_FR]

    def _items_header_row():
        """Shaded header. In the reference 'S.No' is NOT bold and is #515151;
        the other five are bold #000000."""
        def cell(txt, bold=True):
            return Paragraph(txt, _style(
                '_SH', fontSize=9, fontName=FONT_B if bold else FONT,
                textColor=colors.black if bold else BODY,
                leading=12, alignment=TA_CENTER))
        return [cell('S.No', bold=False), cell(LBL['date_col']),
                cell('Employee Name'), cell(LBL['role_col']),
                cell(LBL['days_col']), cell('Amount (₹)')]

    def _dt(dt_val, fmt='%d-%b-%y'):
        if not dt_val:
            return '—'
        try:
            return dt_val.strftime(fmt)
        except Exception:
            return str(dt_val)[:10]

    def header():
        """Logo | company block. Grid 2620/6780 twips in the reference.

        Carries the company name and CIN only — GSTIN and PAN are deliberately
        not printed here (they remain on the profile and are used elsewhere)."""
        co_st  = _style('SFC', fontSize=16, fontName=FONT_B, textColor=CO_COLOR, leading=20)
        sub_st = _style('SFS', fontSize=9,  textColor=BODY, leading=12)

        right = [Paragraph(ctx['s_name'] or '', co_st), Spacer(1, 2)]
        cin = (s.get('cin') or '').strip()
        if cin:
            right.append(Paragraph(f"CIN: {cin}", sub_st))

        # A list cell must hold flowables only — use a bare '' when there's no logo.
        left = [ctx['logo_img']] if ctx['logo_img'] else ''
        tbl = Table([[left, right]], colWidths=[CW * 2620 / 9400.0, CW * 6780 / 9400.0])
        tbl.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('LEFTPADDING',  (0, 0), (-1, -1), 0),
            ('RIGHTPADDING', (0, 0), (-1, -1), 0),
            ('TOPPADDING',   (0, 0), (-1, -1), 0),
            ('BOTTOMPADDING',(0, 0), (-1, -1), 6),
        ]))
        return [tbl, Spacer(1, 8)]

    def meta():
        """Customer ID# row + a two-cell Billing Period row (the reference puts
        the from/to dates in separate centered, bold, shaded cells)."""
        lbl = _style('SML', fontSize=8, textColor=BODY, leading=11)
        val = _style('SMV', fontSize=8, textColor=BODY, leading=11)
        dt_ = _style('SMD', fontSize=8, fontName=FONT_B, textColor=colors.black,
                     leading=11, alignment=TA_CENTER)

        half = CW / 2.0
        data = [
            [Paragraph(LBL['meta_id'], lbl), Paragraph(inv.invoice_number or '—', val)],
            [Paragraph(LBL['period'], lbl), ''],
            [Paragraph(_dt(inv.invoice_date), dt_), Paragraph(_dt(inv.due_date), dt_)],
        ]
        tbl = Table(data, colWidths=[half, half])
        tbl.setStyle(TableStyle([
            ('VALIGN',       (0, 0), (-1, -1), 'MIDDLE'),
            ('LEFTPADDING',  (0, 0), (-1, -1), 6),
            ('RIGHTPADDING', (0, 0), (-1, -1), 6),
            ('TOPPADDING',   (0, 0), (-1, -1), 4),
            ('BOTTOMPADDING',(0, 0), (-1, -1), 4),
            ('SPAN',       (0, 1), (1, 1)),
            ('BACKGROUND', (0, 0), (-1, 0), STEEL),   # Customer ID# row
            ('BACKGROUND', (0, 2), (-1, 2), STEEL),   # the two date cells
            ('BOX',        (0, 0), (-1, -1), 0.4, STEEL),
            ('INNERGRID',  (0, 0), (-1, -1), 0.4, STEEL),
        ]))
        return [tbl]

    def bill_to():
        """'From' banner + seller identity, then 'Bill to' banner + two columns:
        left Customer/CIN/PAN/GST, right a nested Recipient Address / Phone
        table (reference grid 1280/3220).

        The header masthead already carries the company name + CIN, so this
        block prints the seller's GST and PAN — CIN is not repeated here."""
        lbl = _style('SBL', fontSize=8, textColor=BODY, leading=11)
        val = _style('SBV', fontSize=8, textColor=BODY, leading=11)

        def pair(label, value):
            return Paragraph(f"{label} <b>{value}</b>", val) if value else None

        seller_cell = [p for p in (
            pair('Company', ctx['s_name'] or ''),
            pair('GST', s.get('gst') or ''),
            pair('PAN', s.get('pan') or ''),
        ) if p is not None]

        left = [p for p in (
            pair('Customer', inv.customer_name or ''),
            pair('CIN', s.get('customer_cin') or ''),
            pair('PAN', s.get('customer_pan') or ''),
            pair('GST', getattr(inv, 'customer_gst', '') or ''),
        ) if p is not None]

        addr_lines = [ln for ln in (getattr(inv, 'customer_address', '') or '').split('\n') if ln.strip()]
        recipient  = (s.get('recipient') or '').strip()
        phone      = (s.get('customer_phone') or '').strip()

        # Recipient / address / phone sit under the customer's own details in the
        # right-hand column.
        if recipient:
            left.append(Paragraph(f"Recipient <b>{recipient}</b>", val))
        for ln in addr_lines:
            left.append(Paragraph(ln, val))
        if phone:
            left.append(Paragraph(f"Phone <b>{phone}</b>", val))

        half = CW / 2.0
        # FROM | BILL TO side by side, two banner cells over two detail cells.
        tbl = Table([[Paragraph('From', lbl), Paragraph('Bill to', lbl)],
                     [seller_cell, left]],
                    colWidths=[half, half])
        tbl.setStyle(TableStyle([
            ('VALIGN',       (0, 0), (-1, -1), 'TOP'),
            ('LEFTPADDING',  (0, 0), (-1, -1), 6),
            ('RIGHTPADDING', (0, 0), (-1, -1), 6),
            ('TOPPADDING',   (0, 0), (-1, -1), 4),
            ('BOTTOMPADDING',(0, 0), (-1, -1), 4),
            ('BACKGROUND', (0, 0), (-1, 0), STEEL),   # banner row
            ('BOX',       (0, 0), (-1, -1), 0.4, STEEL),
            ('INNERGRID', (0, 0), (-1, -1), 0.4, STEEL),
        ]))
        return [tbl, Spacer(1, 10)]

    def items():
        no_st  = _style('SIN', fontSize=9, textColor=BODY, leading=12, alignment=TA_CENTER)
        tx_st  = _style('SIT', fontSize=8.5, textColor=DARK, leading=12)
        amt_st = _style('SIA', fontSize=8.5, fontName=FONT_B, textColor=DARK, leading=12,
                         alignment=TA_RIGHT)

        data = [_items_header_row()]

        invoice_items = list(inv.items) if inv.items else []
        for idx, it in enumerate(invoice_items, 1):
            emp_name  = it.product_name or '—'
            role      = getattr(it, 'description', '') or ''
            proj_date = it.hsn_code or ''
            days      = int(it.quantity) if it.quantity == int(it.quantity) else float(it.quantity)
            # Pre-tax line amount. `it.total` is tax-INCLUSIVE (calculate_totals:
            # line_after_discount + line_tax), so using it made the column sum to
            # the grand total while the "Total" row below showed the subtotal.
            disc      = float(getattr(it, 'discount', 0) or 0)
            amount    = round(float(it.unit_price or 0) * float(it.quantity or 0) * (1 - disc / 100), 2)

            row_bg = colors.Color(0.612, 0.761, 0.894, 0.08) if idx % 2 == 0 else None

            data.append([
                Paragraph(f"{idx:02d}", no_st),
                Paragraph(proj_date, tx_st),
                Paragraph(emp_name, tx_st),
                Paragraph(role, tx_st),
                Paragraph(str(days), _style('_D', fontSize=8.5, textColor=DARK,
                                              leading=12, alignment=TA_CENTER)),
                Paragraph(_fmt(amount), amt_st),
            ])

        tbl = Table(data, colWidths=COL_W, repeatRows=1, splitByRow=1)
        style_cmds = [
            ('BACKGROUND',   (0, 0), (-1, 0), STEEL),
            ('VALIGN',       (0, 0), (-1, -1), 'MIDDLE'),
            ('LEFTPADDING',  (0, 0), (-1, -1), 4),
            ('RIGHTPADDING', (0, 0), (-1, -1), 4),
            ('TOPPADDING',   (0, 0), (-1, -1), 5),
            ('BOTTOMPADDING',(0, 0), (-1, -1), 5),
            ('LINEBELOW',    (0, 0), (-1, -1), 0.4, BLUE_LT),
            ('LINEBELOW',    (0, 0), (-1, 0), 0, WHITE),  # suppress rule under header
        ]
        # Even-row tint
        for i in range(2, len(data), 2):
            style_cmds.append(('BACKGROUND', (0, i), (-1, i), colors.Color(0.612, 0.761, 0.894, 0.08)))
        tbl.setStyle(TableStyle(style_cmds))
        return [tbl]

    def totals():
        """Full-width totals ladder matching the reference document:
        Total / CGST / SGST / IGST / Total GST / Grand Total, closed by a
        shaded 'Amount in Words' strip.  Label spans cols 0-4, value in col 5."""
        cur = ctx['cur']
        cgst, sgst, igst, *_ = _gst_split(ctx)
        gst_total = round(cgst + sgst + igst, 2)
        # Derive the combined rate from the amounts actually charged, not from
        # the three configured rates: an intra-state invoice carries an IGST
        # rate of 9% with a zero amount, and summing rates would print 27%
        # against an 18% figure.
        base = float(inv.subtotal or 0)
        gst_rate_lbl = f"{round(gst_total / base * 100, 2):g}" if base else '0'

        # Reference emphasis: ONLY the "Total" row and the "Amount in Words"
        # strip are shaded. Grand Total is plain 8pt body text like the rest.
        lbl = _style('SFTL', fontSize=8, textColor=BODY, leading=11)
        val = _style('SFTV', fontSize=8, textColor=BODY, leading=11,
                     alignment=TA_RIGHT, splitLongWords=0)
        lbl_b = _style('SFTLB', fontSize=9, fontName=FONT_B, textColor=TOT_LBL, leading=12)
        val_b = _style('SFTVB', fontSize=9, fontName=FONT_B, textColor=TOT_LBL, leading=12,
                       alignment=TA_RIGHT, splitLongWords=0)

        rows = [( 'Total', _fmt(inv.subtotal, cur), True)]
        rows += [(lbl_txt, _fmt(amt, cur), False) for lbl_txt, amt in _active_gst_rows(ctx)]
        rows += [
            (f'Total GST @ {gst_rate_lbl}%', _fmt(gst_total, cur),       False),
            (LBL['grand'],                   _fmt(inv.grand_total, cur), False),
        ]

        # The reference restates the six-column header above the totals block.
        data = [_items_header_row()]
        cmds = [
            ('VALIGN',       (0, 0), (-1, -1), 'MIDDLE'),
            ('LEFTPADDING',  (0, 0), (-1, -1), 4),
            ('RIGHTPADDING', (0, 0), (-1, -1), 4),
            ('TOPPADDING',   (0, 0), (-1, -1), 5),
            ('BOTTOMPADDING',(0, 0), (-1, -1), 5),
            ('LINEBELOW',    (0, 0), (-1, -1), 0.4, BLUE_LT),
            ('BACKGROUND',   (0, 0), (-1, 0), STEEL),
        ]
        for i, (label, amount, strong) in enumerate(rows, start=1):
            data.append([Paragraph(label, lbl_b if strong else lbl), '', '', '', '',
                         Paragraph(amount, val_b if strong else val)])
            cmds.append(('SPAN', (0, i), (4, i)))
            if strong:
                cmds.append(('BACKGROUND', (0, i), (-1, i), STEEL))

        total_val = float(inv.grand_total or 0)
        words = _amount_words(total_val) if total_val > 0 else ''
        if words:
            r = len(data)
            data.append([Paragraph(f'<b>Amount in Words:</b>&nbsp; {words}',
                                   _style('SFTW', fontSize=9, textColor=colors.black,
                                          leading=12, alignment=TA_CENTER)),
                         '', '', '', '', ''])
            cmds += [('SPAN', (0, r), (-1, r)), ('BACKGROUND', (0, r), (-1, r), STEEL)]

        tbl = Table(data, colWidths=COL_W)
        tbl.setStyle(TableStyle(cmds))
        # The ladder is read as one unit — without this it can break between
        # "Total GST" and "Grand Total", stranding half of it on the previous
        # page. KeepTogether pushes the whole block to the next page instead.
        return [KeepTogether([tbl]), Spacer(1, 10)]

    def notes():
        # The form posts the thank-you message as `notes`, and footer() already
        # prints it as the reference's centered blue line — a Notes block here
        # would repeat the same sentence. The reference has no Notes section.
        return []

    def terms():
        return _terms_sec(ctx)

    def signature():
        # dept + thank-you live in footer() for this template — don't repeat them
        return _signature_sec(ctx, show_dept=False, stamp=False)

    def footer():
        """Thank-you block only. The address / website / contact lines sit at the
        bottom of EVERY page and are drawn by _staffing_page_footer's canvas
        callback, not here.

        The reference puts the two lines at different sizes AND colors inside one
        centered block: 12pt #2D74B5 then 8pt #ED0000."""
        thanks = (s.get('thankyou_msg') or inv.notes or 'Thank you for your business!').strip()
        dept   = (s.get('department') or '').strip()
        if not thanks and not dept:
            return []
        parts = []
        if thanks:
            parts.append(f'<font size="12" color="#2D74B5">{thanks}</font>')
        if dept:
            parts.append(f'<font size="8" color="#ED0000">{dept}</font>')
        block = '<br/>'.join(parts)
        return [Spacer(1, 6),
                Paragraph(block, _style('SFFH', fontSize=12, leading=16,
                                        alignment=TA_CENTER))]

    return dict(header=header, invoice_meta=meta, bill_to=bill_to, items_table=items,
                totals=totals, notes=notes, terms=terms, signature=signature, footer=footer)


SECTION_BUILDERS = {
    'classic':      _sec_classic,
    'minimal':      _sec_minimal,
    'modern':       _sec_modern,
    'professional': _sec_professional,
    'bold':         _sec_bold,
    'compact':      _sec_compact,
    'sidebar':      _sec_sidebar,
    'boxed':        _sec_boxed,
    'statement':    _sec_statement,
    'staffing':     _sec_staffing,
    'payroll':      _sec_staffing,   # same engine, payroll labels via ctx['style_id']
}


# ══════════════════════════════════════════════════════════════════════════════
# Public entry point
# ══════════════════════════════════════════════════════════════════════════════

def _resolve_plan(invoice):
    """Best-effort plan lookup for the owning user (for gated-template fallback)."""
    try:
        from apps.authentication.models import User
        u = User.objects(pk=str(invoice.created_by)).first()
        if u and getattr(u, 'role', '') == 'superadmin':
            return 'unlimited'
    except Exception:
        pass
    try:
        from apps.subscriptions.entitlements import plan_for_user_id
        return plan_for_user_id(invoice.created_by)
    except Exception:
        return 'free'


def _effective_hidden(cfg):
    """Merge new-style hidden_sections with legacy flat toggles."""
    hidden = set(cfg.get('hidden_sections', []))
    is_new_style = bool(cfg.get('section_order_raw') or cfg.get('is_new_style'))
    if not is_new_style:
        if cfg.get('show_terms') is False:
            hidden.add('terms')
        if cfg.get('show_signature') is False:
            hidden.add('signature')
    return hidden


# Templates whose web-preview .pvw-bar bottom strip uses the secondary
# (dark) color instead of the accent color — mirrors the CSS overrides in
# form.html (.tpl-minimal/.tpl-modern/.tpl-sidebar/.tpl-boxed/.tpl-statement
# .pvw-bar{background:var(--tpl-secondary)}).
_BAR_USES_SECONDARY = {'minimal', 'modern', 'sidebar', 'boxed', 'statement'}

# Templates that render their own footer block and must NOT get the solid
# bottom strip appended — 'staffing' reproduces the reference document's
# centered address / website / contact lines inside its own footer() section.
_BAR_SUPPRESSED = {'staffing', 'payroll'}


def _bottom_bar(style_id, spec, ACCENT, s_name, CW, seller=None):
    """Solid-color strip at the very bottom of the document, matching the
    web preview's `.pvw-bar`. The logged-in preview centres the company name;
    the no-login preview joins website | email | phone, so mirror whichever
    flow produced the invoice."""
    bar_color = colors.HexColor(spec['secondary']) if style_id in _BAR_USES_SECONDARY else ACCENT
    label = s_name or ''
    # Only the no-login flow swaps the bar for website | email | phone. The
    # logged-in flow now also carries a seller dict, so gate on `source` —
    # a bare truthiness test here would restyle all nine other templates.
    if seller and (seller.get('source', 'sidecar') == 'sidecar'):
        # Each part drops when empty (matching the HSN/SAC column's rule) —
        # this used to fall back to Zayron's own website, which put our
        # domain on invoices that belong to whoever filled in the form.
        parts = [seller.get('website') or '', seller.get('email') or '', seller.get('phone') or '']
        label = '   |   '.join([x for x in parts if x])
    txt = Paragraph(label, _style('BAR', fontSize=7.5, fontName=FONT_B,
                                                textColor=WHITE, alignment=TA_CENTER))
    bar = Table([[txt]], colWidths=[CW])
    bar.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), bar_color),
        ('TOPPADDING', (0, 0), (-1, -1), 6), ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
    ]))
    return [Spacer(1, 10), bar]


# Vertical space the staffing page footer needs: 3 lines @ 8pt + leading + gap.
_STAFFING_FOOTER_H = 1.3 * cm


def _staffing_page_footer(seller, page_w):
    """Canvas callback drawing the reference document's three centered footer
    lines at the bottom of EVERY page (in the source they are typed manually at
    the foot of each page, so a one-shot flowable at end-of-story is wrong).

    The contact line mixes three colors in one centered run — phone black,
    the pipe #2D74B5, the email #40ACD1 — so it is laid out by measuring each
    fragment rather than with a single drawCentredString.
    """
    s = seller or {}
    addr    = (s.get('address') or '').replace('\n', ', ').strip()
    website = (s.get('website') or '').strip()
    phone   = (s.get('phone') or '').strip()
    email   = (s.get('email') or '').strip()

    GREY = colors.HexColor('#515151')
    LINK = colors.HexColor('#40ACD1')
    PIPE = colors.HexColor('#2D74B5')

    def draw(canvas, doc):
        canvas.saveState()
        canvas.setFont(FONT, 8)
        y = 0.95 * cm + 2 * 11          # baseline of the first of three lines
        cx = page_w / 2.0
        if addr:
            canvas.setFillColor(GREY)
            canvas.drawCentredString(cx, y, addr)
        y -= 11
        if website:
            canvas.setFillColor(LINK)
            canvas.drawCentredString(cx, y, website)
        y -= 11
        if phone or email:
            frags = []
            if phone:
                frags.append((f"Mobile: {phone} ", colors.black))
            if phone and email:
                frags.append(("|", PIPE))
            if email:
                frags.append((email, LINK))
            total = sum(canvas.stringWidth(t, FONT, 8) for t, _ in frags)
            x = cx - total / 2.0
            for text, col in frags:
                canvas.setFillColor(col)
                canvas.drawString(x, y, text)
                x += canvas.stringWidth(text, FONT, 8)
        canvas.restoreState()

    return draw


def build_elements(style_id, invoice, s_info, logo_img, ACCENT, cur, cfg, CW, spec=None,
                   seller=None):
    s_name, s_email, s_phone, s_gst, s_addr = s_info
    spec = spec or get_template_spec(style_id)
    ctx = {
        'inv': invoice, 'cur': cur, 'ACCENT': ACCENT, 'cfg': cfg, 'CW': CW,
        's_name': s_name, 's_email': s_email, 's_phone': s_phone,
        's_gst': s_gst, 's_addr': s_addr, 'logo_img': logo_img,
        'spec': spec, 'style_id': style_id, 'seller': seller,
    }
    builders = SECTION_BUILDERS.get(style_id, _sec_classic)
    sections = builders(ctx)
    hidden = _effective_hidden(cfg)
    elems = []
    for sid in cfg['section_order']:
        if sid in hidden:
            continue
        fn = sections.get(sid)
        if fn is None:
            continue
        out = fn()
        if out:
            elems += out
    if style_id not in _BAR_SUPPRESSED:
        bar_name = getattr(invoice, 'signature_company', '') or s_name
        elems += _bottom_bar(style_id, spec, ACCENT, bar_name, CW, seller=seller)
    return elems


def _render_via_browser(invoice, business_profile, seller, style_id, filepath):
    """Print templates/invoices/print.html with headless Chrome.

    That page includes the same markup + CSS partials the live preview renders,
    so the PDF is a print of the preview rather than a second, separate layout.
    Returns True when a PDF was produced; False makes the caller fall back to
    the ReportLab path (no Chrome installed, render error, …).
    """
    try:
        from django.template.loader import render_to_string
        from .print_context import build_print_context
        from .html_pdf import html_to_pdf

        ctx = build_print_context(invoice, business_profile, seller, style_id)
        # Chrome loads the page over file://, so media URLs have to be absolute
        # paths it can actually open.
        for key in ('logo_url', 'sig_url'):
            rel = ctx.get(key) or ''
            if rel and not rel.startswith(('http', 'data:', 'file:')):
                p = os.path.join(settings.MEDIA_ROOT, rel.replace(settings.MEDIA_URL, '', 1).lstrip('/'))
                ctx[key] = 'file:///' + p.replace('\\', '/') if os.path.exists(p) else ''
        return html_to_pdf(render_to_string('invoices/print.html', ctx), filepath)
    except Exception:
        logger.warning("browser PDF path failed; falling back to ReportLab", exc_info=True)
        return False


# Templates rendered by printing the live-preview markup instead of ReportLab.
# Widen this as each remaining template's preview reaches parity.
_BROWSER_RENDERED = {'staffing', 'payroll'}


def generate_invoice_pdf(invoice, business_profile=None, seller=None, plan=None) -> str:
    filename = f"{invoice.invoice_number}.pdf"
    filepath = os.path.join(settings.MEDIA_ROOT, 'pdfs', filename)
    os.makedirs(os.path.dirname(filepath), exist_ok=True)

    W, H = A4
    L = R = 1.8 * cm
    T = B = 1.5 * cm
    CW = W - L - R

    requested = getattr(invoice, 'template_style', 'classic') or 'classic'
    if plan is None:
        plan = _resolve_plan(invoice)
    style_id = resolve_template(requested, plan)
    spec = get_template_spec(style_id)

    # Both flows onto one dict, so ctx['seller'] is never None (see _merge_seller).
    seller = _merge_seller(business_profile, seller, invoice=invoice)

    # Preferred path: print the live-preview markup with headless Chrome so the
    # PDF and the preview cannot diverge. Falls through to ReportLab below if
    # no browser is available.
    if style_id in _BROWSER_RENDERED:
        if _render_via_browser(invoice, business_profile, seller, style_id, filepath):
            return f"pdfs/{filename}"

    # Staffing draws its address/website/contact block on every page via a canvas
    # callback, so the frame has to give that space back or content overlaps it.
    if style_id in ('staffing', 'payroll'):
        B = B + _STAFFING_FOOTER_H

    doc = SimpleDocTemplate(filepath, pagesize=A4,
                            leftMargin=L, rightMargin=R,
                            topMargin=T, bottomMargin=B)

    ACCENT = _accent(invoice, spec)
    cur    = invoice.currency

    cfg = normalize_layout_config(getattr(invoice, 'layout_config', None))
    cfg['is_new_style'] = bool((getattr(invoice, 'layout_config', None) or {}).get(
        'section_order') or (getattr(invoice, 'layout_config', None) or {}).get('hidden_sections'))

    s_name, s_email, s_phone, s_gst, s_addr, logo_path = _seller_info(business_profile, seller,
                                                                      invoice=invoice)
    max_logo_pt = min(cfg['logo_width'] * 0.75, 6 * cm)  # css px -> pdf pt
    # Square bound — without an explicit max_h, _logo_image()'s own 2.5cm
    # default height silently flattened out most of the slider's range.
    logo_img = _logo_image(logo_path, max_w=max_logo_pt, max_h=max_logo_pt)

    elems = build_elements(style_id, invoice, (s_name, s_email, s_phone, s_gst, s_addr),
                           logo_img, ACCENT, cur, cfg, CW, spec=spec, seller=seller)
    if style_id in ('staffing', 'payroll'):
        cb = _staffing_page_footer(seller, W)
        doc.build(elems, onFirstPage=cb, onLaterPages=cb)
    else:
        doc.build(elems)
    return f"pdfs/{filename}"
