import os
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.lib import colors
from reportlab.platypus import (SimpleDocTemplate, Table, TableStyle,
                                Paragraph, Spacer, HRFlowable, KeepTogether, Image)
from reportlab.lib.enums import TA_RIGHT, TA_CENTER, TA_LEFT
from django.conf import settings


# ── Palette ───────────────────────────────────────────────────────────────────
DARK   = colors.HexColor('#0F172A')
MUTED  = colors.HexColor('#64748B')
BORDER = colors.HexColor('#E2E8F0')
ROW_ALT= colors.HexColor('#F8FAFC')
WHITE  = colors.white
BAL_BG = colors.HexColor('#1E293B')   # dark bg for Balance Due row

STATUS_COLORS = {
    'Paid':      colors.HexColor('#15803D'),
    'Draft':     MUTED,
    'Partial':   colors.HexColor('#D97706'),
    'Overdue':   colors.HexColor('#B91C1C'),
    'Cancelled': MUTED,
}


def _accent(invoice):
    """Return accent color from invoice.template_color or default orange."""
    hex_val = getattr(invoice, 'template_color', None) or '#F97316'
    try:
        return colors.HexColor(hex_val)
    except Exception:
        return colors.HexColor('#F97316')


def _style(name, **kw):
    d = dict(fontName='Helvetica', fontSize=9, textColor=DARK, leading=13, spaceAfter=0)
    d.update(kw)
    return ParagraphStyle(name, **d)


def _fmt(amount, currency='INR'):
    symbol = 'Rs.' if currency == 'INR' else currency + ' '
    return f"{symbol}{float(amount):,.2f}"


def _logo_image(logo_path_str):
    """Return a ReportLab Image scaled to fit 3.5cm × 2.5cm, or None."""
    if not logo_path_str:
        return None
    logo_abs = os.path.join(settings.MEDIA_ROOT, logo_path_str)
    if not os.path.exists(logo_abs):
        return None
    try:
        from PIL import Image as PILImage
        with PILImage.open(logo_abs) as pil:
            pw, ph = pil.size
        scale = min((3.5 * cm) / pw, (2.5 * cm) / ph) if pw and ph else 1
        return Image(logo_abs, width=pw * scale, height=ph * scale)
    except Exception:
        return Image(logo_abs, width=3.5 * cm, height=2.5 * cm)


def _seller_info(business_profile, seller):
    bp = business_profile
    s  = seller or {}
    name  = (bp.company_name if bp and bp.company_name else s.get('name', ''))
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


def generate_invoice_pdf(invoice, business_profile=None, seller=None) -> str:
    filename = f"{invoice.invoice_number}.pdf"
    filepath = os.path.join(settings.MEDIA_ROOT, 'pdfs', filename)
    os.makedirs(os.path.dirname(filepath), exist_ok=True)

    W, H  = A4
    L = R = 1.8 * cm
    T = B = 1.5 * cm
    CW = W - L - R

    doc = SimpleDocTemplate(filepath, pagesize=A4,
                            leftMargin=L, rightMargin=R,
                            topMargin=T, bottomMargin=B)
    elems = []
    cur    = invoice.currency
    ACCENT = _accent(invoice)
    # build per-invoice status colors with accent for 'Sent'
    s_colors = dict(STATUS_COLORS)
    s_colors['Sent'] = ACCENT

    # ── Seller info ───────────────────────────────────────────────────────────
    s_name, s_email, s_phone, s_gst, s_addr, logo_path = \
        _seller_info(business_profile, seller)
    logo_img = _logo_image(logo_path)

    # ── 1. Header: "Invoice" heading LEFT  |  Logo RIGHT ─────────────────────
    inv_label = Paragraph(
        'Invoice',
        _style('ILbl', fontSize=26, fontName='Helvetica-Bold', textColor=DARK, leading=30))

    inv_num_para = Paragraph(
        invoice.invoice_number,
        _style('INum', fontSize=9, textColor=MUTED, leading=13))

    # Nested table stacks heading + number properly in one cell
    left_header = Table(
        [[inv_label], [inv_num_para]],
        colWidths=[CW * 0.6],
    )
    left_header.setStyle(TableStyle([
        ('LEFTPADDING',   (0, 0), (-1, -1), 0),
        ('RIGHTPADDING',  (0, 0), (-1, -1), 0),
        ('TOPPADDING',    (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING', (0, 0), (0, 0),   2),
        ('BOTTOMPADDING', (0, 1), (0, 1),   0),
    ]))

    logo_cell = logo_img if logo_img else Paragraph('', _style('empty'))

    hdr_tbl = Table(
        [[left_header, logo_cell]],
        colWidths=[CW * 0.6, CW * 0.4],
    )
    hdr_tbl.setStyle(TableStyle([
        ('VALIGN',        (0, 0), (-1, -1), 'TOP'),
        ('ALIGN',         (1, 0), (1, 0),   'RIGHT'),
        ('LEFTPADDING',   (0, 0), (-1, -1), 0),
        ('RIGHTPADDING',  (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
    ]))
    elems.append(hdr_tbl)
    elems.append(Spacer(1, 14))
    elems.append(HRFlowable(width=CW, thickness=1.5, color=BORDER, spaceAfter=14))

    # ── 2. From | Bill To (two columns) ──────────────────────────────────────
    lbl_st  = _style('Lbl2', fontSize=7.5, fontName='Helvetica-Bold',
                     textColor=MUTED, leading=10, spaceAfter=5)
    head_st = _style('FHd', fontSize=10, fontName='Helvetica-Bold',
                     textColor=DARK, leading=14)
    body_st = _style('FBd', fontSize=8.5, textColor=MUTED, leading=12)

    def _addr_block(label, lines):
        block = [Paragraph(label, lbl_st)]
        first = True
        for ln in lines:
            if not ln:
                continue
            block.append(Paragraph(ln, head_st if first else body_st))
            first = False
        if first:  # nothing added after label
            block.append(Paragraph('—', body_st))
        return block

    # From block
    from_lines = [s_name, s_email, s_addr, s_phone]
    if s_gst:
        from_lines.append(f"GST: {s_gst}")
    from_block = _addr_block('FROM', from_lines)

    # Bill To block — customer_phone not on model, fall back to sidecar
    cust_phone = (seller or {}).get('customer_phone', '') if not business_profile else ''
    to_lines = [invoice.customer_name, invoice.customer_email,
                getattr(invoice, 'customer_address', '') or '',
                cust_phone]
    if invoice.customer_gst:
        to_lines.append(f"GST: {invoice.customer_gst}")
    to_block = _addr_block('BILL TO', to_lines)

    from_to_tbl = Table(
        [[from_block, to_block]],
        colWidths=[CW * 0.5, CW * 0.5],
    )
    from_to_tbl.setStyle(TableStyle([
        ('VALIGN',        (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING',   (0, 0), (-1, -1), 0),
        ('RIGHTPADDING',  (0, 0), (-1, -1), 0),
        ('TOPPADDING',    (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
    ]))
    elems.append(from_to_tbl)
    elems.append(Spacer(1, 14))
    elems.append(HRFlowable(width=CW, thickness=0.5, color=BORDER, spaceAfter=10))

    # ── 3. Invoice meta: Number | Date | Terms ────────────────────────────────
    inv_date = invoice.invoice_date.strftime('%d %b %Y') if invoice.invoice_date else '—'
    due_date = invoice.due_date.strftime('%d %b %Y')     if invoice.due_date     else '—'
    terms    = invoice.terms or '—'

    meta_lbl = _style('MLbl', fontSize=7.5, fontName='Helvetica-Bold',
                      textColor=MUTED, leading=10, spaceAfter=3)
    meta_val = _style('MVal', fontSize=9.5, fontName='Helvetica-Bold',
                      textColor=DARK, leading=13)

    status_color = s_colors.get(invoice.status, MUTED)
    status_badge = Table(
        [[Paragraph(invoice.status.upper(),
                    _style('SB', fontSize=7.5, fontName='Helvetica-Bold',
                           textColor=WHITE, alignment=TA_CENTER))]],
        colWidths=[2.2 * cm],
    )
    status_badge.setStyle(TableStyle([
        ('BACKGROUND',    (0, 0), (-1, -1), status_color),
        ('TOPPADDING',    (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ('LEFTPADDING',   (0, 0), (-1, -1), 6),
        ('RIGHTPADDING',  (0, 0), (-1, -1), 6),
    ]))

    meta_tbl = Table(
        [[Paragraph('NUMBER',   meta_lbl), Paragraph('DATE',    meta_lbl),
          Paragraph('TERMS',    meta_lbl), Paragraph('STATUS',  meta_lbl)],
         [Paragraph(invoice.invoice_number, meta_val),
          Paragraph(inv_date,  meta_val),
          Paragraph(terms,     meta_val),
          status_badge]],
        colWidths=[CW * 0.28, CW * 0.22, CW * 0.27, CW * 0.23],
    )
    meta_tbl.setStyle(TableStyle([
        ('VALIGN',        (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING',   (0, 0), (-1, -1), 0),
        ('RIGHTPADDING',  (0, 0), (-1, -1), 0),
        ('TOPPADDING',    (0, 0), (-1, -1), 2),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
    ]))
    elems.append(meta_tbl)
    elems.append(Spacer(1, 14))

    # ── 4. Items table ────────────────────────────────────────────────────────
    th  = _style('TH', fontSize=8, fontName='Helvetica-Bold',
                 textColor=MUTED, alignment=TA_LEFT)
    thr = _style('THR', fontSize=8, fontName='Helvetica-Bold',
                 textColor=MUTED, alignment=TA_RIGHT)

    col_w = [CW * 0.50, CW * 0.17, CW * 0.13, CW * 0.20]

    rows = [[
        Paragraph('DESCRIPTION', th),
        Paragraph('RATE',        thr),
        Paragraph('QTY',         thr),
        Paragraph('AMOUNT',      thr),
    ]]

    # header bottom border only
    items_tbl_style = [
        ('LINEBELOW',     (0, 0), (-1, 0),  1.5, ACCENT),
        ('LINEBELOW',     (0, 1), (-1, -1), 0.4, BORDER),
        ('ROWBACKGROUNDS',(0, 1), (-1, -1), [WHITE, ROW_ALT]),
        ('TOPPADDING',    (0, 0), (-1, -1), 7),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 7),
        ('LEFTPADDING',   (0, 0), (-1, -1), 0),
        ('RIGHTPADDING',  (0, 0), (-1, -1), 0),
        ('VALIGN',        (0, 0), (-1, -1), 'MIDDLE'),
    ]

    for item in invoice.items:
        desc = item.product_name
        if getattr(item, 'description', ''):
            desc += f'\n<font size="7.5" color="#94A3B8">{item.description}</font>'
        rows.append([
            Paragraph(desc, _style('Desc', fontSize=9, textColor=DARK, leading=13)),
            Paragraph(_fmt(item.unit_price, cur),
                      _style('RR', fontSize=9, textColor=DARK, alignment=TA_RIGHT)),
            Paragraph(f"{float(item.quantity):g}",
                      _style('QR', fontSize=9, textColor=DARK, alignment=TA_RIGHT)),
            Paragraph(_fmt(item.total, cur),
                      _style('AR', fontSize=9, fontName='Helvetica-Bold',
                             textColor=DARK, alignment=TA_RIGHT)),
        ])

    items_tbl = Table(rows, colWidths=col_w, repeatRows=1)
    items_tbl.setStyle(TableStyle(items_tbl_style))
    elems.append(items_tbl)
    elems.append(Spacer(1, 10))

    # ── 5. Totals: Subtotal | Total | Balance Due ─────────────────────────────
    tot_lbl = _style('TL', fontSize=9,  textColor=MUTED, alignment=TA_RIGHT)
    tot_val = _style('TV', fontSize=9,  fontName='Helvetica-Bold',
                     textColor=DARK, alignment=TA_RIGHT)
    bal_lbl = _style('BL', fontSize=10, fontName='Helvetica-Bold',
                     textColor=WHITE, alignment=TA_RIGHT)
    bal_val = _style('BV', fontSize=10, fontName='Helvetica-Bold',
                     textColor=WHITE, alignment=TA_RIGHT)

    tw = CW * 0.38   # totals block width
    totals_data = [
        [Paragraph('Subtotal',   tot_lbl),
         Paragraph(_fmt(invoice.subtotal,   cur), tot_val)],
        [Paragraph('Tax',        tot_lbl),
         Paragraph(_fmt(invoice.tax_amount, cur), tot_val)],
        [Paragraph('Total',      tot_lbl),
         Paragraph(_fmt(invoice.grand_total, cur), tot_val)],
        [Paragraph('Balance Due', bal_lbl),
         Paragraph(_fmt(invoice.grand_total, cur), bal_val)],
    ]
    totals_tbl = Table(totals_data, colWidths=[tw * 0.55, tw * 0.45])
    totals_tbl.setStyle(TableStyle([
        ('LEFTPADDING',   (0, 0), (-1, -1), 6),
        ('RIGHTPADDING',  (0, 0), (-1, -1), 6),
        ('TOPPADDING',    (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ('LINEBELOW',     (0, 0), (-1, 2),  0.5, BORDER),
        ('BACKGROUND',    (0, 3), (-1, 3),  ACCENT),
    ]))

    wrapper = Table([[totals_tbl]], colWidths=[tw], hAlign='RIGHT')
    wrapper.setStyle(TableStyle([
        ('LEFTPADDING',  (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('TOPPADDING',   (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING',(0, 0), (-1, -1), 0),
        ('BOX',          (0, 0), (-1, -1), 0.5, BORDER),
    ]))
    elems.append(wrapper)

    # ── 6. Notes & Terms ─────────────────────────────────────────────────────
    notes_parts = []
    if invoice.notes:
        notes_parts += [
            Spacer(1, 14),
            HRFlowable(width=CW, thickness=0.5, color=BORDER, spaceAfter=8),
            Paragraph('Notes', _style('NH', fontSize=8.5, fontName='Helvetica-Bold',
                                      textColor=DARK, spaceAfter=3)),
            Paragraph(invoice.notes, _style('NT', fontSize=8.5, textColor=MUTED, leading=13)),
        ]
    if invoice.terms:
        notes_parts += [
            Spacer(1, 8) if not invoice.notes else Spacer(1, 6),
            Paragraph('Terms & Conditions',
                      _style('TH2', fontSize=8.5, fontName='Helvetica-Bold',
                             textColor=DARK, spaceAfter=3)),
            Paragraph(invoice.terms, _style('NT2', fontSize=8.5, textColor=MUTED, leading=13)),
        ]
    if notes_parts:
        elems += notes_parts

    # ── 7. Footer ─────────────────────────────────────────────────────────────
    elems.append(Spacer(1, 20))
    elems.append(HRFlowable(width=CW, thickness=0.5, color=BORDER, spaceAfter=6))
    elems.append(Paragraph(
        f"Computer-generated invoice &nbsp;|&nbsp; InvoiceMS &nbsp;|&nbsp; {invoice.invoice_number}",
        _style('Ft', fontSize=7.5, textColor=MUTED, alignment=TA_CENTER)
    ))

    doc.build(elems)
    return f"pdfs/{filename}"
