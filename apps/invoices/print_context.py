"""Server-side twin of form.html's updatePvw().

Builds the context that _invoice_doc.html renders, so the print page (and
therefore the Chrome-generated PDF) shows exactly what the live preview shows.
Formatting here mirrors the preview's JS helpers deliberately:

    fmtRs   -> _rs()      '₹' + toLocaleString('en-IN', 2dp)
    dshort  -> _dshort()  %d-%m-%Y

Keep the two in step: a change to one without the other reintroduces exactly the
preview/PDF drift this module exists to remove.
"""
import re as _re

from .pdf_generator import _gst_split, _amount_words, _merge_seller
from .template_registry import get_template_spec

# Reference items grid, same fractions _sec_staffing and the preview use.
_STAFFING_GRID = '7.48% 15.61% 26.88% 24.10% 11.55% 14.39%'
# Task templates share the staffing document engine (same grid); only the
# column labels differ.
_TASK_COLS = {
    'staffing': ['S.No', 'DATE', 'EMPLOYEE', 'ROLE', 'DAYS', 'AMOUNT'],
    'payroll':  ['S.No', 'PAY PERIOD', 'EMPLOYEE', 'DESIGNATION', 'DAYS', 'AMOUNT'],
}


def _rs(n):
    """'₹1,23,456.00' — Indian grouping, matching fmtRs()'s en-IN locale."""
    try:
        n = float(n or 0)
    except (TypeError, ValueError):
        n = 0.0
    whole, frac = divmod(round(abs(n) * 100), 100)
    s = str(int(whole))
    if len(s) > 3:                      # 12,34,567 grouping
        head, tail = s[:-3], s[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        s = ','.join(parts + [tail])
    return ('-' if n < 0 else '') + '₹' + s + '.' + f'{int(frac):02d}'


def _dshort(dt):
    if not dt:
        return '—'
    try:
        return f"{dt.day:02d}-{dt.month:02d}-{dt.year}"
    except Exception:
        return str(dt)[:10]


def _num(q):
    q = float(q or 0)
    return str(int(q)) if q == int(q) else str(q)


def _item_date(v):
    """YYYY-MM-DD -> DD-MM-YY, matching fmtItemDate() in the preview.

    The employee/onboarding line editor uses <input type="date">, which stores
    ISO. Rows typed before that (free text) pass through unchanged.
    """
    v = (v or '').strip()
    m = _re.match(r'^(\d{4})-(\d{2})-(\d{2})$', v)
    return f"{m.group(3)}-{m.group(2)}-{m.group(1)[2:]}" if m else v


def build_print_context(invoice, business_profile=None, seller=None, style_id=None):
    """Context for invoices/print.html — the same values updatePvw() computes."""
    s = _merge_seller(business_profile, seller, invoice=invoice) or {}
    style_id = style_id or (getattr(invoice, 'template_style', '') or 'classic')
    spec = get_template_spec(style_id)
    # Task templates (staffing / payroll) share one document engine: the
    # same meta block, address banners and items grid, with payroll-only
    # labels where they differ.
    staffing = style_id in ('staffing', 'payroll')
    payroll = style_id == 'payroll'

    cgst, sgst, igst, c_rate, s_rate, i_rate = _gst_split({
        'inv': invoice, 'seller': s})
    gst_total = round(cgst + sgst + igst, 2)
    base = float(invoice.subtotal or 0)
    gst_lbl = f"{round(gst_total / base * 100, 2):g}" if base else '0'

    co_name = (s.get('name') or '').strip()
    cin, gst, pan = (s.get('cin') or ''), (s.get('gst') or ''), (s.get('pan') or '')

    # Items — staffing relabels the columns; everything else keeps the
    # DESCRIPTION/QTY/RATE/SET%/AMOUNT shape.
    items = list(invoice.items or [])

    # A column nobody filled in is dropped, exactly as the live preview does it
    # (form.html's updatePvw) and as _items_rows() does for the PDF. Rendering a
    # fixed five-column table here was why the print page and the customer's
    # approval page showed no HSN/SAC on an invoice whose preview and PDF had one.
    _iss = (getattr(invoice, 'layout_config', None) or {}).get('section_settings', {})
    _its = (_iss or {}).get('items_table', {}) or {}
    show_hsn = _its.get('show_hsn', True) and any(
        (getattr(i, 'hsn_code', '') or '').strip() for i in items)
    show_disc = _its.get('show_discount', True) and any(
        float(getattr(i, 'discount', 0) or 0) > 0 for i in items)

    rows = []
    for idx, it in enumerate(items, 1):
        disc = float(getattr(it, 'discount', 0) or 0)
        # Pre-tax line value, so the AMOUNT column adds up to Subtotal. GST is
        # then added once, in the CGST/SGST rows below.
        amt = round(float(it.unit_price or 0) * float(it.quantity or 0) * (1 - disc / 100), 2)
        if staffing:
            rows.append([f"{idx:02d}", _item_date(it.hsn_code), it.product_name or '—',
                         getattr(it, 'description', '') or '', _num(it.quantity), _rs(amt)])
        else:
            row = [getattr(it, 'description', '') or it.product_name or '—']
            if show_hsn:
                row.append((getattr(it, 'hsn_code', '') or '').strip())
            row += [_num(it.quantity), _rs(it.unit_price)]
            if show_disc:
                row.append(f"{disc:g}%" if disc else '')
            row.append(_rs(amt))
            rows.append(row)

    if staffing:
        gen_cols, gen_grid = None, _STAFFING_GRID
    else:
        gen_cols = (['DESCRIPTION'] + (['HSN'] if show_hsn else [])
                    + ['QTY', 'RATE'] + (['SET%'] if show_disc else []) + ['AMOUNT'])
        # Same track widths the preview sets inline.
        gen_grid = ('1fr ' + ('32px ' if show_hsn else '') + '32px 55px '
                    + ('32px ' if show_disc else '') + '55px').strip()

    ctx = {
        'style_id': style_id, 'staffing': staffing,
        # User's chosen accent colour wins over the template's default.
        'accent': (getattr(invoice, 'template_color', '') or spec['accent']),
        'secondary': spec['secondary'],
        'text': spec['text'], 'muted': spec['muted'],
        'border': spec['border'], 'tint': spec['tint'],

        # _merge_seller supplies these as `logo_path` / `sig_path` (the names the
        # sidecar and BusinessProfile use). Reading logo_url/sig_url here left
        # both permanently blank, so the logo and signature vanished from the
        # browser-rendered PDF. Either may be a MEDIA-relative path OR a base64
        # data: URL (signature_image is stored as a data URL) — both are passed
        # through untouched and resolved to file:// in _render_via_browser.
        'logo_url': s.get('logo_path') or '',
        'co_name': co_name.upper(),
        # Staffing's masthead carries CIN; every other template shows GSTIN.
        'co_gst': (f"CIN: {cin}" if cin else '') if staffing else (f"GSTIN: {gst}" if gst else ''),

        'inv_no': invoice.invoice_number or '—',
        'inv_date': _dshort(getattr(invoice, 'invoice_date', None)),
        'cust_id': invoice.invoice_number or '—',
        'cust_id_lbl': 'Payroll ID' if payroll else 'Number / Customer ID',
        'bp_from': _dshort(getattr(invoice, 'invoice_date', None)),
        'bp_to': _dshort(getattr(invoice, 'due_date', None)),
        'period_lbl': 'Pay Period' if payroll else 'Billing Period',

        'from_name': co_name,
        'from_addr': '' if staffing else (s.get('address') or ''),
        # Identifiers print once: staffing's masthead carries CIN so its FROM
        # block keeps the GSTIN; every other template shows GSTIN in the header
        # and leaves it out of the FROM block.
        'from_cin': '',
        'from_gst': (f"GSTIN: {gst}" if gst and staffing else ''),
        'from_pan': (f"PAN: {pan}" if pan and staffing else ''),

        'to_name': invoice.customer_name or '—',
        'to_recipient': (f"Attn: {s['recipient']}" if s.get('recipient') else ''),
        'to_addr': getattr(invoice, 'customer_address', '') or '',
        'to_email': invoice.customer_email or '',
        'to_phone': s.get('customer_phone') or '',
        'to_cin': (f"CIN: {s['customer_cin']}" if s.get('customer_cin') else ''),
        'to_gst': (f"GSTIN: {invoice.customer_gst}" if invoice.customer_gst else ''),
        'to_pan': (f"PAN: {s['customer_pan']}" if s.get('customer_pan') else ''),

        'item_cols': _TASK_COLS.get(style_id) if staffing else gen_cols,
        'items_grid': gen_grid,
        'item_rows': rows,

        'sub_lbl': 'Total' if staffing else 'Subtotal',
        'sub': _rs(invoice.subtotal),
        # Print renders statically — hide GST rows whose component doesn't
        # apply (CGST+SGST and IGST are mutually exclusive).
        'hide_inactive_gst': True,
        # An empty label hides the row in _invoice_doc.html — CGST+SGST and
        # IGST are mutually exclusive, so only the applying rows print.
        'cgst_lbl': f'CGST @{c_rate}%' if (float(c_rate or 0) > 0 or cgst > 0) else '',
        'cgst': _rs(cgst),
        'sgst_lbl': f'SGST @{s_rate}%' if (float(s_rate or 0) > 0 or sgst > 0) else '',
        'sgst': _rs(sgst),
        'igst_lbl': f'IGST @{i_rate}%' if (float(i_rate or 0) > 0 or igst > 0) else '',
        'igst': _rs(igst),
        'gsttot_lbl': f'Total GST @{gst_lbl}%', 'gsttot': _rs(gst_total),
        'gst_total': gst_total,
        'grand_lbl': 'Net Pay' if payroll else ('Grand Total (Including GST)' if staffing else 'GRAND TOTAL'),
        'grand': _rs(invoice.grand_total),
        'words': _amount_words(float(invoice.grand_total or 0)) if float(invoice.grand_total or 0) > 0 else '',

        'notes': getattr(invoice, 'notes', '') or '',
        'hide_notes': not (getattr(invoice, 'notes', '') or '').strip(),
        # No invented terms: an invoice without terms prints no Terms block at all.
        'terms': (getattr(invoice, 'terms', '') or '').strip(),
        'hide_terms': not (getattr(invoice, 'terms', '') or '').strip(),
        'sig_name': s.get('sig_name') or '',
        'sig_url': s.get('sig_path') or '',
        'sig_dt': (f"Signed on {s['sig_datetime']}" if s.get('sig_datetime') else ''),
        'dept': '' if staffing else (s.get('department') or ''),

        'foot_thanks': (s.get('thankyou_msg') or '') if staffing else '',
        'foot_dept': (s.get('department') or '') if staffing else '',
        'foot_addr': (s.get('address') or '').replace('\n', ', ') if staffing else '',
        'foot_web': (s.get('website') or '') if staffing else '',
        'foot_phone': (s.get('phone') or '') if staffing else '',
        'foot_email': (s.get('email') or '') if staffing else '',
        'bar': s.get('sig_company') or co_name or 'Your Company Name',
    }
    return ctx
