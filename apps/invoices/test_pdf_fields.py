"""Self-check: every field the no-login generator collects must reach the PDF.

Run: python apps/invoices/test_pdf_fields.py

Regression guard for the free-flow PDF dropping signature, notes, amount-in-words,
CIN/PAN/phone and the real CGST/SGST rates (they live in the seller sidecar, not
on the Invoice document).
"""
import os
import re
import sys
import zlib
import base64

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

import django  # noqa: E402
django.setup()

from reportlab.lib.pagesizes import A4  # noqa: E402
from reportlab.lib.units import cm  # noqa: E402
from reportlab.platypus import SimpleDocTemplate  # noqa: E402

from apps.invoices.pdf_generator import build_elements, _amount_words  # noqa: E402
from apps.invoices.template_registry import normalize_layout_config  # noqa: E402


class _Item:
    product_id = 'free'
    product_name = 'mobile'
    description = ''
    hsn_code = '8517'
    unit = 'Nos'
    unit_price = 10000.0
    quantity = 1
    tax_rate = 0          # the free flow taxes at invoice level, not per line
    discount = 0
    subtotal = 10000.0
    tax_amount = 0
    total = 10000.0


class _Invoice:
    invoice_number = 'inv011'
    customer_name = 'nishek'
    customer_email = 'test@gnail.com'
    customer_address = 'Some street'
    customer_gst = '29AABCP1234D1Z5'
    invoice_date = __import__('datetime').datetime(2026, 9, 6)
    due_date = invoice_date
    items = [_Item()]
    subtotal = 10000.0
    tax_amount = 1800.0
    grand_total = 11800.0
    status = 'Draft'
    notes = ''            # free flow posts '' — thank-you text is in the sidecar
    terms = 'Due on Receipt'
    currency = 'INR'
    template_style = 'classic'
    layout_config = {}
    created_by = 'anonymous'
    created_at = invoice_date


SELLER = {
    'name': 'teck', 'email': 'test@gmail.com', 'phone': '456787657',
    'address': 'sdfghjkl', 'gst': 'gfkagk', 'cin': 'U72900KA2020PTC131313',
    'pan': 'AABCP1234D', 'website': 'www.teck.in', 'logo_path': '',
    'customer_phone': '9998887776', 'customer_pan': 'ZZXCV1234K',
    'recipient': '', 'cgst_rate': 9.0, 'sgst_rate': 9.0,
    'cgst_amt': 900.0, 'sgst_amt': 900.0,
    'thankyou_msg': 'Thank you for your business!',
    'department': 'Sales & Procurement Department',
    'sig_name': 'Nishek K', 'sig_company': 'Teck Pvt Ltd', 'sig_path': '',
}


def _pdf_text(path):
    """Pull the visible Tj strings out of the single-page content stream."""
    d = open(path, 'rb').read()
    objs = {int(m.group(1)): m.group(2)
            for m in re.finditer(rb'(\d+) 0 obj(.*?)endobj', d, re.S)}
    page = next(v for v in objs.values() if b'/Type /Page' in v and b'/Contents' in v)
    num = int(re.search(rb'/Contents (\d+) 0 R', page).group(1))
    body = objs[num]
    head, raw = body[:body.find(b'stream')], body[body.find(b'stream') + 6:]
    raw = raw.lstrip(b'\r\n')
    raw = raw[:raw.rfind(b'endstream')]
    if b'ASCII85' in head:
        raw = base64.a85decode(raw.strip().rstrip(b'~>'))
    content = zlib.decompress(raw).decode('latin1')
    return '\n'.join(t[1:t.rfind(')')]
                     for t in re.findall(r'\((?:\.|[^\()])*\)\s*Tj', content))


def _render(seller):
    from apps.invoices.pdf_generator import _accent, get_template_spec
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), '_test_invoice.pdf')
    W, H = A4
    CW = W - 2 * 1.8 * cm
    inv = _Invoice()
    spec = get_template_spec('classic')
    cfg = normalize_layout_config(inv.layout_config)
    elems = build_elements('classic', inv, (seller.get('name'), seller.get('email'),
                                            seller.get('phone'), seller.get('gst'),
                                            seller.get('address')),
                           None, _accent(inv, spec), 'INR', cfg, CW,
                           spec=spec, seller=seller)
    SimpleDocTemplate(out, pagesize=A4, leftMargin=1.8 * cm, rightMargin=1.8 * cm,
                      topMargin=1.5 * cm, bottomMargin=1.5 * cm).build(elems)
    return _pdf_text(out), out


def main():
    assert _amount_words(11800) == 'Eleven Thousand Eight Hundred Rupees Only.', _amount_words(11800)
    assert _amount_words(1) == 'One Rupee Only.'
    assert _amount_words(10000000.5) == 'One Crore Rupees and Fifty Paise Only.', _amount_words(10000000.5)
    assert _amount_words(0) == 'Zero Rupees Only.'

    text, out = _render(SELLER)
    expected = {
        'signature label':  'Authorised Signatory',
        'signatory name':   'Nishek K',
        'signature company': 'Teck Pvt Ltd',
        'department':       'Sales & Procurement Department',
        'notes heading':    'Notes',
        'thank-you note':   'Thank you for your business!',
        'amount in words':  'Eleven Thousand Eight Hundred Rupees Only.',
        'seller CIN':       'CIN: U72900KA2020PTC131313',
        'seller PAN':       'PAN: AABCP1234D',
        'customer phone':   '9998887776',
        'customer PAN':     'PAN: ZZXCV1234K',
        'CGST rate label':  'CGST @9%',
        'SGST rate label':  'SGST @9%',
        'footer bar':       'www.teck.in',
    }
    missing = [f'{k} ({v!r})' for k, v in expected.items() if v not in text]
    assert not missing, 'missing from PDF: ' + '; '.join(missing)
    # Intra-state default: no IGST row may print.
    assert 'IGST' not in text, 'IGST @0% row must drop out on intra-state invoices'

    # A one-item invoice must still fit on one page — the footer stack is tall
    # enough that a stray spacer orphans the colour bar onto page 2.
    pages = len(re.findall(rb'/Type /Page[^s]', open(out, 'rb').read()))
    assert pages == 1, f'one-item invoice spilled onto {pages} pages'

    # IGST-only invoice: CGST/SGST rows must drop out — the two modes are
    # mutually exclusive (inter-state vs intra-state supply). Re-renders to
    # the same temp path, so it must run after the page-count check above.
    igst_seller = dict(SELLER, cgst_rate=0.0, sgst_rate=0.0, igst_rate=18.0,
                       cgst_amt=0.0, sgst_amt=0.0, igst_amt=1800.0)
    igst_text, _ = _render(igst_seller)
    assert 'CGST' not in igst_text and 'SGST' not in igst_text, \
        'CGST/SGST rows must drop out on IGST-only invoices'
    assert 'IGST @18%' in igst_text, 'IGST row missing on IGST-only invoice'

    # Logged-in flow (no sidecar) keeps its old shape: no amount-in-words band.
    plain, _ = _render({})
    assert 'AMOUNT IN WORDS' not in plain
    assert 'Authorised Signatory' in plain, 'signature must not be hidden by default'

    os.remove(out)
    print('OK — all no-login generator fields reach the PDF')


if __name__ == '__main__':
    main()
