import csv
import io
from datetime import datetime

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

EMBER = colors.HexColor('#C1121F')
DARK = colors.HexColor('#131416')
MUTED = colors.HexColor('#717585')
BORDER = colors.HexColor('#E8E7E5')
ZEBRA = colors.HexColor('#FAF9F8')

PAY_COLORS = {
    'Paid': ('#DCFCE7', '#15803D'),
    'Due': ('#FEF3C7', '#B45309'),
    'Overdue': ('#FEE2E2', '#B91C1C'),
    'Cancelled': ('#F1F5F9', '#64748B'),
}
APPROVAL_COLORS = {
    'Approved': '#15803D',
    'Disapproved': '#B91C1C',
    'In Process': '#717585',
}


def payment_label(inv, now=None):
    now = now or datetime.utcnow()
    if inv.status in ('Paid', 'Cancelled'):
        return inv.status
    due = inv.due_date.replace(tzinfo=None) if inv.due_date else None
    if inv.status == 'Overdue' or (due and due < now):
        return 'Overdue'
    return 'Due'


def _d(dt):
    return dt.strftime('%d %b %Y') if dt else '—'


def _rs(n):
    n = float(n or 0)
    s = f'{abs(n):,.2f}'
    # Indian digit grouping: 12,34,567.89
    whole, frac = s.split('.')
    whole = whole.replace(',', '')
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        head = ','.join([head[max(i - 2, 0):i] for i in range(len(head), 0, -2)][::-1])
        whole = f'{head},{tail}'
    return f"{'-' if n < 0 else ''}Rs. {whole}.{frac}"


def summarize(invoices):
    now = datetime.utcnow()
    out = {'count': 0, 'billed': 0.0, 'paid': 0.0, 'due': 0.0, 'overdue': 0.0,
           'overdue_count': 0, 'cancelled': 0}
    for inv in invoices:
        out['count'] += 1
        amt = float(inv.grand_total or 0)
        label = payment_label(inv, now)
        if label == 'Cancelled':
            out['cancelled'] += 1
            continue
        out['billed'] += amt
        if label == 'Paid':
            out['paid'] += amt
        else:
            out['due'] += amt
            if label == 'Overdue':
                out['overdue'] += amt
                out['overdue_count'] += 1
    return out


def build_csv(invoices):
    buf = io.StringIO()
    buf.write('﻿')  # BOM so Excel opens the rupee symbol and UTF-8 names correctly
    w = csv.writer(buf)
    w.writerow(['Invoice #', 'Customer', 'Customer Email', 'Invoice Date', 'Due Date',
                'Subtotal', 'Tax', 'Grand Total', 'Payment', 'Status', 'Approval'])
    now = datetime.utcnow()
    for inv in invoices:
        w.writerow([
            inv.invoice_number, inv.customer_name, getattr(inv, 'customer_email', '') or '',
            _d(inv.invoice_date), _d(inv.due_date),
            f'{float(getattr(inv, "subtotal", 0) or 0):.2f}',
            f'{float(getattr(inv, "tax_total", 0) or 0):.2f}',
            f'{float(inv.grand_total or 0):.2f}',
            payment_label(inv, now), inv.status, inv.approval_status or 'In Process',
        ])
    return buf.getvalue().encode('utf-8')


def build_pdf(invoices, company='', filters_text='All invoices', generated_by=''):
    buf = io.BytesIO()
    page = landscape(A4)
    margin = 14 * mm
    doc = SimpleDocTemplate(buf, pagesize=page, leftMargin=margin, rightMargin=margin,
                            topMargin=38 * mm, bottomMargin=16 * mm,
                            title='Invoice Report', author=company or 'Zayro Invoice')
    width = page[0] - 2 * margin
    generated = datetime.now().strftime('%d %b %Y, %I:%M %p')

    def chrome(canvas, _doc):
        canvas.saveState()
        w, h = page
        canvas.setFillColor(EMBER)
        canvas.rect(0, h - 28 * mm, w, 28 * mm, fill=1, stroke=0)
        canvas.setFillColor(colors.HexColor('#8C0F1B'))
        canvas.rect(0, h - 28 * mm, w, 1.2 * mm, fill=1, stroke=0)
        canvas.setFillColor(colors.white)
        canvas.setFont('Helvetica-Bold', 17)
        canvas.drawString(margin, h - 13 * mm, 'Invoice Report')
        canvas.setFont('Helvetica', 9)
        canvas.drawString(margin, h - 19.5 * mm, (company or 'Zayro Invoice') + '   ·   ' + filters_text)
        canvas.setFont('Helvetica', 8.5)
        canvas.drawRightString(w - margin, h - 13 * mm, f'Generated {generated}')
        if generated_by:
            canvas.drawRightString(w - margin, h - 19.5 * mm, f'by {generated_by}')
        canvas.setStrokeColor(BORDER)
        canvas.line(margin, 11 * mm, w - margin, 11 * mm)
        canvas.setFillColor(MUTED)
        canvas.setFont('Helvetica', 8)
        canvas.drawString(margin, 7 * mm, 'Zayro Invoice · Smart Billing')
        canvas.drawRightString(w - margin, 7 * mm, f'Page {_doc.page}')
        canvas.restoreState()

    s = summarize(invoices)
    rate = round(s['paid'] / s['billed'] * 100) if s['billed'] else 0
    lbl = ParagraphStyle('lbl', fontName='Helvetica-Bold', fontSize=7.5, textColor=MUTED, leading=10)
    tiles = [
        ('TOTAL INVOICES', str(s['count']), f"{_rs(s['billed'])} billed", DARK),
        ('COLLECTED', _rs(s['paid']), f'{rate}% collection rate', colors.HexColor('#15803D')),
        ('OUTSTANDING', _rs(s['due']), 'awaiting payment', colors.HexColor('#B45309')),
        ('OVERDUE', _rs(s['overdue']), f"{s['overdue_count']} invoice(s) past due", colors.HexColor('#B91C1C')),
        ('CANCELLED', str(s['cancelled']), 'excluded from totals', MUTED),
    ]
    cells = []
    for title, val, sub, col in tiles:
        v = ParagraphStyle('v', fontName='Helvetica-Bold', fontSize=13, textColor=col, leading=16)
        sb = ParagraphStyle('sb', fontName='Helvetica', fontSize=7.5, textColor=MUTED, leading=10)
        cells.append([Paragraph(title, lbl), Paragraph(val, v), Paragraph(sub, sb)])
    tile_w = (width - 4 * 3 * mm) / 5
    tile_tables = []
    for c, (_, _, _, col) in zip(cells, tiles):
        t = Table([[x] for x in c], colWidths=[tile_w - 1])
        t.setStyle(TableStyle([
            ('BOX', (0, 0), (-1, -1), 0.6, BORDER),
            ('LINEBEFORE', (0, 0), (0, -1), 2.5, col),
            ('BACKGROUND', (0, 0), (-1, -1), colors.white),
            ('LEFTPADDING', (0, 0), (-1, -1), 9), ('TOPPADDING', (0, 0), (-1, 0), 7),
            ('BOTTOMPADDING', (0, -1), (-1, -1), 7), ('TOPPADDING', (0, 1), (-1, -1), 1),
        ]))
        tile_tables.append(t)
    row = []
    for i, t in enumerate(tile_tables):
        row.append(t)
        if i < len(tile_tables) - 1:
            row.append('')
    summary = Table([row], colWidths=sum([[tile_w, 3 * mm] for _ in tiles], [])[:-1])
    summary.setStyle(TableStyle([('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0)]))

    hd = ParagraphStyle('hd', fontName='Helvetica-Bold', fontSize=7.5, textColor=colors.white, leading=10)
    hdr = ParagraphStyle('hdr', parent=hd, alignment=TA_RIGHT)
    cell = ParagraphStyle('c', fontName='Helvetica', fontSize=8.5, textColor=DARK, leading=11)
    num = ParagraphStyle('n', fontName='Helvetica-Bold', fontSize=8.5, textColor=EMBER, leading=11)
    amt = ParagraphStyle('a', fontName='Helvetica-Bold', fontSize=8.5, textColor=DARK, leading=11, alignment=TA_RIGHT)

    data = [[Paragraph(h, hd) for h in ('#', 'INVOICE', 'CUSTOMER', 'INVOICE DATE', 'DUE DATE', 'PAYMENT', 'APPROVAL')]
            + [Paragraph('AMOUNT', hdr)]]
    styles = [
        ('BACKGROUND', (0, 0), (-1, 0), DARK),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 6), ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('LEFTPADDING', (0, 0), (-1, -1), 7), ('RIGHTPADDING', (0, 0), (-1, -1), 7),
        ('LINEBELOW', (0, 1), (-1, -1), 0.4, BORDER),
    ]
    now = datetime.utcnow()
    for i, inv in enumerate(invoices, start=1):
        pay = payment_label(inv, now)
        bg, fg = PAY_COLORS[pay]
        appr = inv.approval_status or 'In Process'
        data.append([
            Paragraph(str(i), ParagraphStyle('i', parent=cell, textColor=MUTED)),
            Paragraph(inv.invoice_number or '—', num),
            Paragraph(inv.customer_name or '—', cell),
            Paragraph(_d(inv.invoice_date), cell),
            Paragraph(_d(inv.due_date), cell),
            Paragraph(f'<b>{pay}</b>', ParagraphStyle('p', parent=cell, textColor=colors.HexColor(fg), fontSize=8)),
            Paragraph(appr, ParagraphStyle('ap', parent=cell, fontSize=8,
                                           textColor=colors.HexColor(APPROVAL_COLORS.get(appr, '#717585')))),
            Paragraph(_rs(inv.grand_total), amt),
        ])
        styles.append(('BACKGROUND', (5, i), (5, i), colors.HexColor(bg)))
        if i % 2 == 0:
            styles.append(('BACKGROUND', (0, i), (4, i), ZEBRA))
            styles.append(('BACKGROUND', (6, i), (-1, i), ZEBRA))
        if pay == 'Cancelled':
            styles.append(('TEXTCOLOR', (0, i), (-1, i), MUTED))

    tot = ParagraphStyle('t', fontName='Helvetica-Bold', fontSize=9, textColor=DARK, leading=12, alignment=TA_RIGHT)
    data.append(['', '', '', '', '', '', Paragraph('Total billed', tot), Paragraph(_rs(s['billed']), tot)])
    styles += [('LINEABOVE', (0, -1), (-1, -1), 1.2, DARK), ('SPAN', (0, -1), (5, -1)),
               ('TOPPADDING', (0, -1), (-1, -1), 8)]

    fr = [0.04, 0.15, 0.25, 0.11, 0.11, 0.09, 0.1, 0.15]
    table = Table(data, colWidths=[width * f for f in fr], repeatRows=1)
    table.setStyle(TableStyle(styles))

    story = [summary, Spacer(1, 7 * mm), table]
    if not invoices:
        story = [summary, Spacer(1, 10 * mm),
                 Paragraph('No invoices match these filters.', ParagraphStyle('e', fontSize=10, textColor=MUTED))]
    doc.build(story, onFirstPage=chrome, onLaterPages=chrome)
    return buf.getvalue()
