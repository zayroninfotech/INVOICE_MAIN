from rest_framework.views import APIView
from rest_framework.permissions import AllowAny
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from django.http import FileResponse
from django.conf import settings
from .models import Invoice, InvoiceItem
from .pdf_generator import generate_invoice_pdf
from .template_registry import normalize_layout_config
from utils.response import success, error
from apps.subscriptions.entitlements import can_create_invoice, increment_usage
from datetime import datetime, date
import base64
import os
import re
import uuid
import json


def _seller_dir():
    d = os.path.join(settings.MEDIA_ROOT, 'free_sellers')
    os.makedirs(d, exist_ok=True)
    return d

def _save_seller(invoice_pk, data):
    path = os.path.join(_seller_dir(), f"{invoice_pk}.json")
    with open(path, 'w') as f:
        json.dump(data, f)

def _load_seller(invoice_pk):
    path = os.path.join(_seller_dir(), f"{invoice_pk}.json")
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {}


def _save_upload(file_obj, subdir):
    """Save an uploaded file and return its relative path."""
    ext = file_obj.name.rsplit('.', 1)[-1].lower() if '.' in file_obj.name else 'png'
    filename = f"{subdir}_{uuid.uuid4().hex[:10]}.{ext}"
    folder = os.path.join(settings.MEDIA_ROOT, subdir)
    os.makedirs(folder, exist_ok=True)
    abs_path = os.path.join(folder, filename)
    with open(abs_path, 'wb') as f:
        for chunk in file_obj.chunks():
            f.write(chunk)
    return f"{subdir}/{filename}"


def _save_base64_image(data_url, subdir):
    """Save a base64 data URL as a PNG file and return its relative path."""
    try:
        if ',' in data_url:
            data_url = data_url.split(',', 1)[1]
        img_bytes = base64.b64decode(data_url)
        filename = f"{subdir}_{uuid.uuid4().hex[:10]}.png"
        folder = os.path.join(settings.MEDIA_ROOT, subdir)
        os.makedirs(folder, exist_ok=True)
        abs_path = os.path.join(folder, filename)
        with open(abs_path, 'wb') as f:
            f.write(img_bytes)
        return f"{subdir}/{filename}"
    except Exception:
        return ''



# ── Per-device free limit ─────────────────────────────────────────────────────
# A device is identified by a random id kept in BOTH a long-lived cookie and the
# browser's localStorage (sent as `device_id`), so clearing one doesn't reset it.
FREE_DEVICE_LIMIT = 5
_DEV_RE = re.compile(r'^[a-f0-9]{32}$')


def _device_ids(request):
    ids = []
    for v in (request.COOKIES.get('zi_dev'), request.data.get('device_id') if hasattr(request, 'data') else None):
        v = (v or '').strip().lower()
        if _DEV_RE.match(v) and v not in ids:
            ids.append(v)
    return ids


def _device_usage_coll():
    return Invoice._get_collection().database['free_device_usage']


def _device_used(ids):
    if not ids:
        return 0
    docs = _device_usage_coll().find({'_id': {'$in': ids}}, {'count': 1})
    return max([d.get('count', 0) for d in docs] or [0])


class FreeInvoiceView(APIView):
    permission_classes = [AllowAny]
    parser_classes     = [MultiPartParser, FormParser, JSONParser]

    def post(self, request):
        dev_ids = _device_ids(request) or [uuid.uuid4().hex]
        used = _device_used(dev_ids)
        if used >= FREE_DEVICE_LIMIT:
            return error(
                f"You've used all {FREE_DEVICE_LIMIT} free invoices on this device. "
                "Please sign up or log in to keep creating invoices.",
                {"reason": "device_limit", "signup_required": True, "limit": FREE_DEVICE_LIMIT},
                status=403,
            )
        ok, reason, plan, _ = can_create_invoice(request)
        if not ok:
            return error(
                "You've used your free trial invoice. Sign up to create more.",
                {"reason": reason, "signup_required": True},
                status=403,
            )

        data = request.data

        # ── From (seller) fields ──────────────────────────────────────────────
        from_name    = str(data.get('from_name',    '')).strip()
        from_email   = str(data.get('from_email',   '')).strip()
        from_address = str(data.get('from_address', '')).strip()
        from_phone   = str(data.get('from_phone',   '')).strip()
        from_gst     = str(data.get('from_gst',     '')).strip()
        from_cin     = str(data.get('from_cin',     '')).strip()
        from_pan     = str(data.get('from_pan',     '')).strip()
        from_website = str(data.get('from_website', '')).strip()

        # ── Bill To (customer) fields ─────────────────────────────────────────
        customer_name    = str(data.get('customer_name',    '')).strip()
        customer_email   = str(data.get('customer_email',   '')).strip()
        customer_address = str(data.get('customer_address', '')).strip()
        customer_phone   = str(data.get('customer_phone',   '')).strip()
        customer_gst     = str(data.get('customer_gst',     '')).strip()
        customer_pan     = str(data.get('customer_pan',     '')).strip()
        recipient        = str(data.get('recipient',        '')).strip()

        # ── Invoice meta ──────────────────────────────────────────────────────
        inv_number_custom = str(data.get('invoice_number', '')).strip()
        terms             = str(data.get('terms', '')).strip()
        # Accent colour chosen in the generator preview — must reach the PDF
        # (pdf_generator._accent reads Invoice.template_color).
        template_color = str(data.get('template_color', '') or '').strip()
        if not re.fullmatch(r'#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})', template_color):
            template_color = '#1A3A2A'   # the generator preview's default (Forest Green)

        # ── GST rates ────────────────────────────────────────────────────────
        cgst_rate = float(data.get('cgst_rate', 9) or 0)
        sgst_rate = float(data.get('sgst_rate', 9) or 0)
        igst_rate = float(data.get('igst_rate', 9) or 0)
        # Rates apply exactly as entered: each rate above 0 prints as its own row; 0 drops it.

        # ── Invoice footer / signatory ────────────────────────────────────────
        thankyou_msg = str(data.get('thankyou_msg', '')).strip()
        note_2       = str(data.get('note_2',       '')).strip()
        department   = str(data.get('department',   '')).strip()
        bank_details = str(data.get('bank_details', '')).strip()[:500]
        ship_address = str(data.get('ship_address', '')).strip()[:500]
        sig_name     = str(data.get('sig_name',     '')).strip()
        sig_company  = str(data.get('sig_company',  '')).strip()
        sig_datetime = str(data.get('sig_datetime', '')).strip()[:40]

        # ── Items ─────────────────────────────────────────────────────────────
        raw_items = data.get('items', [])
        if isinstance(raw_items, str):
            try:
                items_data = json.loads(raw_items)
            except (ValueError, TypeError):
                items_data = []
        else:
            items_data = raw_items

        raw_layout = data.get('layout_config') or {}
        if isinstance(raw_layout, str):
            try:
                raw_layout = json.loads(raw_layout)
            except (ValueError, TypeError):
                raw_layout = {}
        layout_config = normalize_layout_config(raw_layout if isinstance(raw_layout, dict) else {})

        if not customer_name or not customer_email:
            return error("Customer name and email are required.")
        if not items_data:
            return error("At least one item is required.")

        built = []
        raw_sub = 0.0
        for it in items_data:
            name  = str(it.get('name', '')).strip()
            hsn   = str(it.get('hsn', '')).strip()
            price = float(it.get('price', 0) or 0)
            qty   = float(it.get('qty', 1) or 1)
            if not name or price <= 0:
                continue
            sub = round(price * qty, 2)
            raw_sub += sub
            # Store 0 tax on item level — we apply CGST+SGST at invoice level
            built.append(InvoiceItem(
                product_id='free', product_name=name, description='',
                hsn_code=hsn, unit='Nos',
                unit_price=price, quantity=qty, tax_rate=0, discount=0,
                subtotal=sub, tax_amount=0, total=sub,
            ))

        if not built:
            return error("No valid items provided.")

        raw_sub    = round(raw_sub, 2)
        cgst_amt   = round(raw_sub * cgst_rate / 100, 2)
        sgst_amt   = round(raw_sub * sgst_rate / 100, 2)
        igst_amt   = round(raw_sub * igst_rate / 100, 2)
        tax_total  = round(cgst_amt + sgst_amt + igst_amt, 2)
        grand_total = round(raw_sub + tax_total, 2)

        free_count = Invoice.objects(created_by='anonymous').count()
        base_number = inv_number_custom or \
            f"INV-{date.today().strftime('%Y%m%d')}-{free_count + 1:03d}"

        # Auto-resolve duplicate invoice numbers — try base, then base-2, base-3 …
        inv_number = base_number
        suffix = 1
        while Invoice.objects(invoice_number=inv_number).first():
            suffix += 1
            inv_number = f"{base_number}-{suffix}"

        invoice = Invoice(
            invoice_number=inv_number,
            customer_id='anonymous',
            customer_name=customer_name,
            customer_email=customer_email,
            customer_address=customer_address,
            customer_gst=customer_gst,
            invoice_date=datetime.utcnow(),
            due_date=datetime.utcnow(),
            items=built,
            subtotal=raw_sub,
            tax_amount=tax_total,
            grand_total=grand_total,
            status='Draft',
            notes='',
            terms=terms,
            currency='INR',
            template_color=template_color,
            layout_config=layout_config,
            created_by='anonymous',
        ).save()

        # ── Save logo ─────────────────────────────────────────────────────────
        logo_path = ''
        logo_file = request.FILES.get('logo')
        if logo_file and logo_file.size > 0:
            logo_path = _save_upload(logo_file, 'free_logos')

        # ── Save signature (upload or base64 draw/type) ───────────────────────
        sig_path = ''
        sig_file = request.FILES.get('signature')
        if sig_file and sig_file.size > 0:
            sig_path = _save_upload(sig_file, 'free_signatures')
        else:
            sig_data = str(data.get('signature_data', '')).strip()
            if sig_data:
                sig_path = _save_base64_image(sig_data, 'free_signatures')

        # ── Persist all seller/extra info for PDF generation ──────────────────
        seller_data = {
            'name': from_name, 'email': from_email,
            'address': from_address, 'phone': from_phone,
            'gst': from_gst, 'cin': from_cin,
            'pan': from_pan, 'website': from_website,
            'logo_path': logo_path,
            'customer_phone': customer_phone,
            'customer_pan': customer_pan,
            'recipient': recipient,
            'cgst_rate': cgst_rate,
            'sgst_rate': sgst_rate,
            'igst_rate': igst_rate,
            'cgst_amt': cgst_amt,
            'sgst_amt': sgst_amt,
            'igst_amt': igst_amt,
            'thankyou_msg': thankyou_msg,
            'note_2': note_2,
            'department': department,
            'bank_details': bank_details,
            'ship_address': ship_address,
            'sig_name': sig_name,
            'sig_company': sig_company,
            'sig_path': sig_path,
            'sig_datetime': sig_datetime,
        }
        _save_seller(str(invoice.pk), seller_data)

        increment_usage(request, 'anonymous', None)
        now_used = used + 1
        for d in dev_ids:   # keep every id this device is known by in step
            _device_usage_coll().update_one(
                {'_id': d},
                {'$set': {'count': now_used, 'last_used': datetime.utcnow(),
                          'ip': (request.META.get('HTTP_X_FORWARDED_FOR') or request.META.get('REMOTE_ADDR') or '').split(',')[0].strip()},
                 '$setOnInsert': {'created': datetime.utcnow()}},
                upsert=True)

        resp = success({
            'free_used': now_used, 'free_limit': FREE_DEVICE_LIMIT,
            'id': str(invoice.pk),
            'invoice_number': invoice.invoice_number,
            'customer_name': invoice.customer_name,
            'customer_email': invoice.customer_email,
            'subtotal': float(raw_sub),
            'tax_amount': float(tax_total),
            'grand_total': float(grand_total),
            'invoice_date': invoice.invoice_date.strftime('%d %b %Y'),
            'items': [
                {'name': i.product_name, 'hsn': i.hsn_code,
                 'qty': float(i.quantity), 'price': float(i.unit_price),
                 'tax': float(i.tax_rate), 'total': float(i.total)}
                for i in invoice.items
            ],
        }, "Free invoice created.", 201)
        resp.set_cookie('zi_dev', dev_ids[0], max_age=5*365*24*3600, httponly=True,
                        samesite='Lax', secure=request.is_secure())
        return resp


class FreeInvoicePDFView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, pk):
        invoice = Invoice.objects(pk=pk, created_by='anonymous').first()
        if not invoice:
            return error("Invoice not found.", status=404)
        seller = _load_seller(str(invoice.pk))
        # Anonymous/no-login flow is restricted to free-tier templates
        # (classic, minimal) via resolve_template()'s plan gating.
        pdf_path = generate_invoice_pdf(invoice, seller=seller, plan='free')
        invoice.pdf_path = pdf_path
        invoice.save()
        full_path = os.path.join(settings.MEDIA_ROOT, pdf_path)
        f = open(full_path, 'rb')
        response = FileResponse(f, content_type='application/pdf',
                                as_attachment=True,
                                filename=f"{invoice.invoice_number}.pdf")
        response['X-Accel-Buffering'] = 'no'
        return response
