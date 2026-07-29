"""
Zayron Infotech branded PDF generator for free invoices.
Produces an A4 invoice matching the orange Zayron design.
"""
import os
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm, mm
from reportlab.lib import colors
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle,
    Paragraph, Spacer, HRFlowable, Image, KeepTogether,
)
from reportlab.lib.enums import TA_RIGHT, TA_CENTER, TA_LEFT
from reportlab.graphics.shapes import Drawing, Circle, String, Line
from reportlab.graphics import renderPDF
from django.conf import settings

# ── Palette ────────────────────────────────────────────────────────────────────
ORANGE  = colors.HexColor('#F97316')
ORANGE_D= colors.HexColor('#EA580C')
DARK    = colors.HexColor('#0F172A')
SLATE   = colors.HexColor('#1E293B')
MUTED   = colors.HexColor('#64748B')
BORDER  = colors.HexColor('#E2E8F0')
ROW_ALT = colors.HexColor('#FAFAFA')
AMBER_BG= colors.HexColor('#FFFBEB')
AMBER_BD= colors.HexColor('#FDE68A')
ORANGE_L= colors.HexColor('#FFF7ED')
WHITE   = colors.white


# ── Helpers ────────────────────────────────────────────────────────────────────
def _st(name, **kw):
    d = dict(fontName='Helvetica', fontSize=9, textColor=DARK, leading=13, spaceAfter=0)
    d.update(kw)
    return ParagraphStyle(name, **d)


def _fmt(n):
    return f"Rs. {float(n):,.2f}"


def _load_image(rel_path, max_w, max_h):
    if not rel_path:
        return None
    abs_path = os.path.join(settings.MEDIA_ROOT, rel_path)
    if not os.path.exists(abs_path):
        return None
    try:
        from PIL import Image as PILImage
        with PILImage.open(abs_path) as pil:
            pw, ph = pil.size
        if pw and ph:
            scale = min(max_w / pw, max_h / ph)
            return Image(abs_path, width=pw * scale, height=ph * scale)
        return Image(abs_path, width=max_w, height=max_h)
    except Exception:
        try:
            return Image(abs_path, width=max_w, height=max_h)
        except Exception:
            return None


def _amount_to_words(n):
    ones = ['', 'One', 'Two', 'Three', 'Four', 'Five', 'Six', 'Seven', 'Eight',
            'Nine', 'Ten', 'Eleven', 'Twelve', 'Thirteen', 'Fourteen', 'Fifteen',
            'Sixteen', 'Seventeen', 'Eighteen', 'Nineteen']
    tens = ['', '', 'Twenty', 'Thirty', 'Forty', 'Fifty',
            'Sixty', 'Seventy', 'Eighty', 'Ninety']

    def tw(x):
        if x < 20:
            return ones[x]
        return tens[x // 10] + (' ' + ones[x % 10] if x % 10 else '')

    def cv(x):
        if not x:
            return ''
        if x < 100:
            return tw(x)
        if x < 1000:
            return ones[x // 100] + ' Hundred' + (' ' + tw(x % 100) if x % 100 else '')
        if x < 100000:
            return cv(x // 1000) + ' Thousand' + (' ' + cv(x % 1000) if x % 1000 else '')
        if x < 10000000:
            return cv(x // 100000) + ' Lakh' + (' ' + cv(x % 100000) if x % 100000 else '')
        return cv(x // 10000000) + ' Crore' + (' ' + cv(x % 10000000) if x % 10000000 else '')

    rupees = int(n)
    paise  = round((n - rupees) * 100)
    words  = cv(rupees) or 'Zero'
    words += ' Rupees'
    if paise:
        words += f' and {tw(paise)} Paise'
    return words + ' Only'


def _rupee_circle_drawing(size=36):
    """Draw an orange filled circle with a white Rs text inside."""
    d = Drawing(size, size)
    c = Circle(size / 2, size / 2, size / 2, fillColor=ORANGE, strokeColor=None)
    d.add(c)
    s = String(size / 2, size / 2 - 6, 'Rs',
               fontName='Helvetica-Bold', fontSize=size * 0.38,
               fillColor=WHITE, textAnchor='middle')
    d.add(s)
    return d


def _thank_you_stamp(size=72):
    """Draw a circular orange-bordered 'Thank YOU!' stamp."""
    d = Drawing(size, size)
    c = Circle(size / 2, size / 2, size / 2 - 2,
               fillColor=WHITE, strokeColor=ORANGE, strokeWidth=2.5)
    d.add(c)
    t1 = String(size / 2, size / 2 + 8, 'Thank',
                fontName='Helvetica-Bold', fontSize=size * 0.16,
                fillColor=ORANGE, textAnchor='middle')
    t2 = String(size / 2, size / 2 - 6, 'YOU!',
                fontName='Helvetica-Bold', fontSize=size * 0.26,
                fillColor=ORANGE, textAnchor='middle')
    d.add(t1)
    d.add(t2)
    return d


# ── Main generator ─────────────────────────────────────────────────────────────
def generate_free_invoice_pdf(invoice, seller=None) -> str:
    s = seller or {}

    filename = f"{invoice.invoice_number}.pdf"
    filepath = os.path.join(settings.MEDIA_ROOT, 'pdfs', filename)
    os.makedirs(os.path.dirname(filepath), exist_ok=True)

    W, H = A4
    LM = RM = 1.6 * cm
    TM = BM = 1.2 * cm
    CW = W - LM - RM

    doc = SimpleDocTemplate(
        filepath, pagesize=A4,
        leftMargin=LM, rightMargin=RM,
        topMargin=TM, bottomMargin=BM,
    )
    elems = []

    # ── Seller info ────────────────────────────────────────────────────────────
    s_name    = s.get('name', '')
    s_addr    = s.get('address', '')
    s_email   = s.get('email', '')
    s_phone   = s.get('phone', '')
    s_gst     = s.get('gst', '')
    s_cin     = s.get('cin', '')
    s_pan     = s.get('pan', '')
    s_website = s.get('website', '')
    logo_path = s.get('logo_path', '')

    cgst_rate = float(s.get('cgst_rate', 9))
    sgst_rate = float(s.get('sgst_rate', 9))
    cgst_amt  = float(s.get('cgst_amt', 0))
    sgst_amt  = float(s.get('sgst_amt', 0))

    sig_name    = s.get('sig_name', '')
    sig_company = s.get('sig_company', '')
    sig_path    = s.get('sig_path', '')
    thankyou_msg= s.get('thankyou_msg', 'Thank you for your business.')

    cust_phone  = s.get('customer_phone', '')
    cust_pan    = s.get('customer_pan', '')
    recipient   = s.get('recipient', '')

    inv_date = invoice.invoice_date.strftime('%d-%m-%Y') if invoice.invoice_date else ''
    sub_total   = float(invoice.subtotal)
    grand_total = float(invoice.grand_total)

    # ══════════════════════════════════════════════════════════════════════════
    # 1. HEADER — logo circle + company name (left) | INVOICE (right)
    # ══════════════════════════════════════════════════════════════════════════
    logo_img = _load_image(logo_path, 3.2 * cm, 2.2 * cm)
    circle_d = _rupee_circle_drawing(40)

    if logo_img:
        brand_cell = logo_img
    else:
        # Company name + circle side by side
        inner = Table(
            [[circle_d,
              [Paragraph(s_name or 'YOUR COMPANY',
                         _st('CN', fontSize=13, fontName='Helvetica-Bold', leading=16)),
               Paragraph('PVT. LTD.' if not s_name else '',
                         _st('PVT', fontSize=7, textColor=ORANGE, leading=10,
                             fontName='Helvetica-Bold', spaceAfter=0))
               ]]],
            colWidths=[1.2 * cm, CW * 0.5 - 1.2 * cm],
        )
        inner.setStyle(TableStyle([
            ('VALIGN',       (0, 0), (-1, -1), 'MIDDLE'),
            ('LEFTPADDING',  (0, 0), (-1, -1), 0),
            ('RIGHTPADDING', (0, 0), (-1, -1), 4),
            ('TOPPADDING',   (0, 0), (-1, -1), 0),
            ('BOTTOMPADDING',(0, 0), (-1, -1), 0),
        ]))
        brand_cell = inner

    invoice_title = Paragraph(
        'INVOICE',
        _st('IT', fontSize=28, fontName='Helvetica-Bold',
            textColor=ORANGE, alignment=TA_RIGHT, leading=32),
    )

    hdr = Table(
        [[brand_cell, invoice_title]],
        colWidths=[CW * 0.55, CW * 0.45],
    )
    hdr.setStyle(TableStyle([
        ('VALIGN',       (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING',  (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('TOPPADDING',   (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING',(0, 0), (-1, -1), 0),
    ]))
    elems.append(hdr)
    elems.append(HRFlowable(width=CW, thickness=3, color=ORANGE, spaceAfter=10))

    # ══════════════════════════════════════════════════════════════════════════
    # 2. COMPANY INFO (left) | INVOICE META (right)
    # ══════════════════════════════════════════════════════════════════════════
    co_lines = []
    if s_name:
        co_lines.append(Paragraph(s_name, _st('CoN', fontSize=10, fontName='Helvetica-Bold', leading=14)))
    if s_addr:
        co_lines.append(Paragraph(s_addr, _st('CoA', fontSize=8, textColor=MUTED, leading=12)))
    if s_email:
        co_lines.append(Paragraph(s_email, _st('CoE', fontSize=8, textColor=MUTED, leading=12)))
    if s_phone:
        co_lines.append(Paragraph(s_phone, _st('CoP', fontSize=8, textColor=MUTED, leading=12)))
    if s_gst:
        co_lines.append(Paragraph(f'GSTIN: {s_gst}', _st('CoG', fontSize=8, textColor=MUTED, leading=12)))
    if s_cin:
        co_lines.append(Paragraph(f'CIN: {s_cin}', _st('CoCIN', fontSize=8, textColor=MUTED, leading=12)))
    if s_pan:
        co_lines.append(Paragraph(f'PAN: {s_pan}', _st('CoPAN', fontSize=8, textColor=MUTED, leading=12)))

    meta_lbl = _st('ML', fontSize=7.5, fontName='Helvetica-Bold', textColor=MUTED, leading=10)
    meta_val = _st('MV', fontSize=9.5, fontName='Helvetica-Bold', textColor=DARK, leading=14)

    meta_rows = [
        [Paragraph('Invoice No.', meta_lbl), Paragraph(':', meta_lbl),
         Paragraph(invoice.invoice_number, meta_val)],
        [Paragraph('Invoice Date', meta_lbl), Paragraph(':', meta_lbl),
         Paragraph(inv_date, meta_val)],
    ]
    meta_tbl = Table(meta_rows, colWidths=[2.4 * cm, 0.3 * cm, CW * 0.42 - 2.7 * cm])
    meta_tbl.setStyle(TableStyle([
        ('LEFTPADDING',  (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('TOPPADDING',   (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING',(0, 0), (-1, -1), 3),
        ('LINEBELOW',    (0, 0), (-1, -2), 0.3, BORDER),
    ]))

    info_tbl = Table(
        [[co_lines, meta_tbl]],
        colWidths=[CW * 0.55, CW * 0.45],
    )
    info_tbl.setStyle(TableStyle([
        ('VALIGN',       (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING',  (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('TOPPADDING',   (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING',(0, 0), (-1, -1), 0),
    ]))
    elems.append(info_tbl)
    elems.append(Spacer(1, 10))

    # ══════════════════════════════════════════════════════════════════════════
    # 3. FROM | BILL TO
    # ══════════════════════════════════════════════════════════════════════════
    addr_hdr = _st('AH', fontSize=8, fontName='Helvetica-Bold',
                   textColor=WHITE, leading=11)
    addr_name = _st('AN', fontSize=9.5, fontName='Helvetica-Bold',
                    textColor=DARK, leading=14)
    addr_body = _st('AB', fontSize=8, textColor=MUTED, leading=12)

    def _addr_cell(title, name_line, lines):
        rows = []
        rows.append(Paragraph(name_line, addr_name) if name_line else Spacer(1, 2))
        for ln in lines:
            if ln:
                rows.append(Paragraph(ln, addr_body))
        return rows

    from_lines = [s_addr, s_email, s_phone]
    if s_gst:
        from_lines.append(f'GSTIN: {s_gst}')
    if s_cin:
        from_lines.append(f'CIN: {s_cin}')
    if s_pan:
        from_lines.append(f'PAN: {s_pan}')

    to_name = invoice.customer_name
    to_lines = []
    if recipient:
        to_lines.append(recipient)
    to_lines.append(getattr(invoice, 'customer_address', '') or '')
    if cust_phone:
        to_lines.append(cust_phone)
    if invoice.customer_email:
        to_lines.append(invoice.customer_email)
    if invoice.customer_gst:
        to_lines.append(f'GSTIN: {invoice.customer_gst}')
    if cust_pan:
        to_lines.append(f'PAN: {cust_pan}')

    from_cell = _addr_cell('FROM', s_name, from_lines)
    to_cell   = _addr_cell('BILL TO', to_name, to_lines)

    half = (CW - 8) / 2

    # Build a single 2-column table: [FROM hdr | BILL TO hdr] then [FROM body | BILL TO body]
    # This guarantees both orange headers sit on exactly the same row.
    addr_wrap = Table(
        [
            # Row 0 — orange header row
            [Paragraph('FROM', addr_hdr),    Paragraph('BILL TO', addr_hdr)],
            # Row 1 — content row
            [from_cell,                       to_cell],
        ],
        colWidths=[half, half],
    )
    addr_wrap.setStyle(TableStyle([
        # Orange header row
        ('BACKGROUND',    (0, 0), (-1, 0), ORANGE),
        # White content rows
        ('BACKGROUND',    (0, 1), (-1, 1), WHITE),
        # Outer border around whole table
        ('BOX',           (0, 0), (-1, -1), 1, BORDER),
        # Vertical divider between the two columns
        ('LINEBEFORE',    (1, 0), (1, -1), 1, WHITE),
        # Header row padding
        ('TOPPADDING',    (0, 0), (-1, 0), 6),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 6),
        # Content row padding
        ('TOPPADDING',    (0, 1), (-1, 1), 8),
        ('BOTTOMPADDING', (0, 1), (-1, 1), 10),
        ('LEFTPADDING',   (0, 0), (-1, -1), 10),
        ('RIGHTPADDING',  (0, 0), (-1, -1), 10),
        ('VALIGN',        (0, 0), (-1, -1), 'TOP'),
    ]))
    elems.append(addr_wrap)
    elems.append(Spacer(1, 12))

    # ══════════════════════════════════════════════════════════════════════════
    # 4. ITEMS TABLE
    # ══════════════════════════════════════════════════════════════════════════
    th_wh = _st('THW', fontSize=9, fontName='Helvetica-Bold', textColor=WHITE, leading=12)
    th_r  = _st('THR', fontSize=9, fontName='Helvetica-Bold', textColor=WHITE,
                alignment=TA_RIGHT, leading=12)
    th_c  = _st('THC', fontSize=9, fontName='Helvetica-Bold', textColor=WHITE,
                alignment=TA_CENTER, leading=12)
    td    = _st('TD',  fontSize=9,  textColor=DARK, leading=14)
    td_r  = _st('TDR', fontSize=9,  textColor=DARK, alignment=TA_RIGHT, leading=14)
    td_rb = _st('TDRB',fontSize=9,  fontName='Helvetica-Bold',
                textColor=DARK, alignment=TA_RIGHT, leading=14)
    td_c  = _st('TDC', fontSize=9,  textColor=MUTED, alignment=TA_CENTER, leading=14)

    # col widths: # | Desc | HSN/SAC | QTY | Rate | GST% | Amount
    # Rate & Amount need enough room for "Rs.1,00,000.00" without wrapping
    c_no   = 0.6  * cm
    c_hsn  = 1.8  * cm
    c_qty  = 1.4  * cm
    c_gst  = 1.4  * cm
    c_rate = 3.0  * cm
    c_amt  = 3.0  * cm
    c_desc = CW - c_no - c_hsn - c_qty - c_gst - c_rate - c_amt
    cw = [c_no, c_desc, c_hsn, c_qty, c_rate, c_gst, c_amt]

    rows = [[
        Paragraph('#', th_c),
        Paragraph('DESCRIPTION', th_wh),
        Paragraph('HSN / SAC', th_c),
        Paragraph('QTY', th_r),
        Paragraph('RATE (Rs.)', th_r),
        Paragraph('GST %', th_r),
        Paragraph('AMOUNT (Rs.)', th_r),
    ]]

    for idx, item in enumerate(invoice.items, 1):
        rows.append([
            Paragraph(str(idx), td_c),
            Paragraph(item.product_name, td),
            Paragraph(item.hsn_code or '', td_c),
            Paragraph(f"{float(item.quantity):g}", td_r),
            Paragraph(f"Rs. {float(item.unit_price):,.2f}", td_r),
            Paragraph(f"{cgst_rate + sgst_rate:.0f}%", td_r),
            Paragraph(f"Rs. {float(item.subtotal):,.2f}", td_rb),
        ])

    # Pad to min 5 rows
    while len(rows) < 6:
        rows.append([Paragraph('', td)] * 7)

    items_tbl = Table(rows, colWidths=cw, repeatRows=1)
    items_tbl.setStyle(TableStyle([
        ('BACKGROUND',    (0, 0), (-1, 0), DARK),
        ('LINEBELOW',     (0, 1), (-1, -1), 0.5, BORDER),
        ('ROWBACKGROUNDS',(0, 1), (-1, -1), [WHITE, ROW_ALT]),
        ('TOPPADDING',    (0, 0), (-1, -1), 9),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 9),
        ('LEFTPADDING',   (0, 0), (-1, -1), 8),
        ('RIGHTPADDING',  (0, 0), (-1, -1), 8),
        ('VALIGN',        (0, 0), (-1, -1), 'MIDDLE'),
        ('BOX',           (0, 0), (-1, -1), 0.5, BORDER),
        # left-align # and Description, center HSN, right-align numbers
        ('ALIGN',         (0, 0), (0, -1), 'CENTER'),
        ('ALIGN',         (1, 0), (1, -1), 'LEFT'),
        ('ALIGN',         (2, 0), (2, -1), 'CENTER'),
        ('ALIGN',         (3, 0), (-1, -1), 'RIGHT'),
    ]))
    elems.append(items_tbl)
    elems.append(Spacer(1, 10))

    # ══════════════════════════════════════════════════════════════════════════
    # 5. AMOUNT IN WORDS (left) | TOTALS (right)
    # ══════════════════════════════════════════════════════════════════════════
    words_str = _amount_to_words(grand_total)

    circ_sz = 1.2 * cm
    words_inner = Table(
        [[_rupee_circle_drawing(32),
          [Paragraph('Amount in Words',
                     _st('WL', fontSize=7.5, fontName='Helvetica-Bold',
                         textColor=ORANGE, leading=10)),
           Paragraph(words_str,
                     _st('WT', fontSize=8, textColor=MUTED, leading=13))
           ]]],
        colWidths=[circ_sz, CW * 0.52 - circ_sz - 16 - 16],
    )
    words_inner.setStyle(TableStyle([
        ('VALIGN',       (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING',  (0, 0), (0, 0), 0),
        ('RIGHTPADDING', (0, 0), (0, 0), 8),
        ('LEFTPADDING',  (1, 0), (1, 0), 0),
        ('RIGHTPADDING', (1, 0), (1, 0), 0),
        ('TOPPADDING',   (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING',(0, 0), (-1, -1), 0),
    ]))

    words_box = Table(
        [[words_inner]],
        colWidths=[CW * 0.52],
    )
    words_box.setStyle(TableStyle([
        ('BOX',          (0, 0), (-1, -1), 0.5, BORDER),
        ('LEFTPADDING',  (0, 0), (-1, -1), 8),
        ('RIGHTPADDING', (0, 0), (-1, -1), 8),
        ('TOPPADDING',   (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING',(0, 0), (-1, -1), 8),
        ('LINEBEFORE',   (0, 0), (0, -1), 3, ORANGE),
    ]))

    tl = _st('TL', fontSize=8.5, textColor=MUTED, alignment=TA_RIGHT)
    tv = _st('TV', fontSize=8.5, fontName='Helvetica-Bold',
             textColor=DARK, alignment=TA_RIGHT)
    tg = _st('TG', fontSize=9, fontName='Helvetica-Bold',
             textColor=ORANGE, alignment=TA_RIGHT)
    tgr = _st('TGR', fontSize=9, fontName='Helvetica-Bold',
              textColor=ORANGE, alignment=TA_RIGHT)
    gw = _st('GW', fontSize=10, fontName='Helvetica-Bold',
             textColor=WHITE, alignment=TA_RIGHT)

    tw_col = CW * 0.46
    tot_rows = [
        [Paragraph('Subtotal',            tl), Paragraph(_fmt(sub_total), tv)],
        [Paragraph(f'CGST @ {cgst_rate:.0f}%', tl), Paragraph(_fmt(cgst_amt), tv)],
        [Paragraph(f'SGST @ {sgst_rate:.0f}%', tl), Paragraph(_fmt(sgst_amt), tv)],
        [Paragraph(f'Total GST (@ {cgst_rate+sgst_rate:.0f}%)', tg),
         Paragraph(_fmt(cgst_amt + sgst_amt), tgr)],
        [Paragraph('GRAND TOTAL', gw), Paragraph(_fmt(grand_total), gw)],
    ]

    tot_tbl = Table(tot_rows, colWidths=[tw_col * 0.55, tw_col * 0.45])
    tot_tbl.setStyle(TableStyle([
        ('LINEBELOW',     (0, 0), (-1, 2), 0.4, BORDER),
        ('LINEABOVE',     (0, 3), (-1, 3), 0.4, BORDER),
        ('BACKGROUND',    (0, 3), (-1, 3), ORANGE_L),
        ('BACKGROUND',    (0, 4), (-1, 4), ORANGE),
        ('TOPPADDING',    (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ('LEFTPADDING',   (0, 0), (-1, -1), 8),
        ('RIGHTPADDING',  (0, 0), (-1, -1), 8),
        ('VALIGN',        (0, 0), (-1, -1), 'MIDDLE'),
        ('BOX',           (0, 0), (-1, -1), 0.5, BORDER),
    ]))

    wt_wrap = Table([[words_box, tot_tbl]], colWidths=[CW * 0.52, CW * 0.48])
    wt_wrap.setStyle(TableStyle([
        ('VALIGN',       (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING',  (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (0, 0), 10),
        ('RIGHTPADDING', (1, 0), (1, 0), 0),
        ('TOPPADDING',   (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING',(0, 0), (-1, -1), 0),
    ]))
    elems.append(wt_wrap)
    elems.append(Spacer(1, 14))
    elems.append(HRFlowable(width=CW, thickness=0.5, color=BORDER, spaceAfter=10))

    # ══════════════════════════════════════════════════════════════════════════
    # 6. FOOTER — Notes | Thank YOU stamp | Authorized Signatory
    # ══════════════════════════════════════════════════════════════════════════
    notes_lines = [
        thankyou_msg,
        'Please make payment on or before the due date.',
        'Goods/Services once delivered are non-refundable unless otherwise agreed.',
    ]
    notes_title = Paragraph('Notes', _st('NT', fontSize=8.5, fontName='Helvetica-Bold',
                                         textColor=DARK, leading=12))
    note_items = [notes_title]
    for nl in notes_lines:
        if nl:
            note_items.append(Paragraph(
                f'• {nl}',
                _st('NB', fontSize=7.5, textColor=MUTED, leading=12, leftIndent=6),
            ))

    stamp_d = _thank_you_stamp(64)

    # Signatory
    sig_label = Paragraph('Authorized Signatory',
                           _st('SL', fontSize=7.5, fontName='Helvetica-Bold',
                               textColor=ORANGE, leading=11, alignment=TA_CENTER))
    sig_img = _load_image(sig_path, 3.5 * cm, 1.4 * cm)
    sig_line = HRFlowable(width=3.5 * cm, thickness=1, color=DARK, spaceAfter=3)
    sig_co   = Paragraph(sig_company or sig_name or '',
                         _st('SCo', fontSize=8, fontName='Helvetica-Bold',
                             textColor=DARK, alignment=TA_CENTER, leading=12))

    sig_cell = [sig_label, Spacer(1, 6)]
    if sig_img:
        sig_cell.append(sig_img)
    else:
        sig_cell.append(Spacer(1, 1.4 * cm))
    sig_cell += [sig_line, sig_co]

    footer_tbl = Table(
        [[note_items, stamp_d, sig_cell]],
        colWidths=[CW * 0.5, CW * 0.18, CW * 0.32],
    )
    footer_tbl.setStyle(TableStyle([
        ('VALIGN',       (0, 0), (-1, -1), 'TOP'),
        ('ALIGN',        (1, 0), (1, 0), 'CENTER'),
        ('ALIGN',        (2, 0), (2, 0), 'CENTER'),
        ('LEFTPADDING',  (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('TOPPADDING',   (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING',(0, 0), (-1, -1), 0),
    ]))
    elems.append(footer_tbl)
    elems.append(Spacer(1, 14))

    # ══════════════════════════════════════════════════════════════════════════
    # 7. BOTTOM ORANGE BAR
    # ══════════════════════════════════════════════════════════════════════════
    bar_st = _st('Bar', fontSize=8, fontName='Helvetica-Bold',
                 textColor=WHITE, alignment=TA_CENTER, leading=11)
    website = s_website or 'www.zayron.in'
    bar_items = []
    if website:
        bar_items.append(website)
    if s_email:
        bar_items.append(s_email)
    if s_phone:
        bar_items.append(s_phone)
    bar_text = '   |   '.join(bar_items) if bar_items else 'Zayron Infotech Pvt. Ltd.'

    bar_tbl = Table(
        [[Paragraph(bar_text, bar_st)]],
        colWidths=[CW],
    )
    bar_tbl.setStyle(TableStyle([
        ('BACKGROUND',   (0, 0), (-1, -1), ORANGE),
        ('TOPPADDING',   (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING',(0, 0), (-1, -1), 8),
        ('LEFTPADDING',  (0, 0), (-1, -1), 10),
        ('RIGHTPADDING', (0, 0), (-1, -1), 10),
    ]))
    elems.append(bar_tbl)

    doc.build(elems)
    return f"pdfs/{filename}"
