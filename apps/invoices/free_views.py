from rest_framework.views import APIView
from rest_framework.permissions import AllowAny
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from django.http import FileResponse
from django.conf import settings
from .models import Invoice, InvoiceItem
from .pdf_generator import generate_invoice_pdf
from utils.response import success, error
from datetime import datetime, date
import mongoengine as me
import os
import uuid
import json


class FreeInvoiceUsage(me.Document):
    ip = me.StringField(required=True)
    date_str = me.StringField(required=True)
    count = me.IntField(default=0)
    meta = {
        'collection': 'free_invoice_usage',
        'indexes': [{'fields': ['ip', 'date_str'], 'unique': True}],
    }


FREE_DAILY_LIMIT = 100


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


def _get_ip(request):
    xff = request.META.get('HTTP_X_FORWARDED_FOR', '')
    return xff.split(',')[0].strip() if xff else request.META.get('REMOTE_ADDR', 'unknown')


class FreeInvoiceView(APIView):
    permission_classes = [AllowAny]
    parser_classes     = [MultiPartParser, FormParser, JSONParser]

    def post(self, request):
        ip = _get_ip(request)
        today = date.today().isoformat()

        usage = FreeInvoiceUsage.objects(ip=ip, date_str=today).first()
        if usage and usage.count >= FREE_DAILY_LIMIT:
            return error(
                f"Daily free limit reached ({FREE_DAILY_LIMIT}/day). "
                "Register for unlimited invoices.",
                status=429
            )

        data = request.data  # works for both JSON and multipart
        # From (seller) fields
        from_name    = str(data.get('from_name', '')).strip()
        from_email   = str(data.get('from_email', '')).strip()
        from_address = str(data.get('from_address', '')).strip()
        from_phone   = str(data.get('from_phone', '')).strip()
        from_gst     = str(data.get('from_gst', '')).strip()

        # Bill To (customer) fields
        customer_name    = str(data.get('customer_name', '')).strip()
        customer_email   = str(data.get('customer_email', '')).strip()
        customer_address = str(data.get('customer_address', '')).strip()
        customer_phone   = str(data.get('customer_phone', '')).strip()
        customer_mobile  = str(data.get('customer_mobile', '')).strip()
        customer_fax     = str(data.get('customer_fax', '')).strip()

        inv_number_custom = str(data.get('invoice_number', '')).strip()
        terms             = str(data.get('terms', '')).strip()
        notes             = str(data.get('notes', '')).strip()

        # items may arrive as a JSON string (FormData) or a list (JSON body)
        raw_items = data.get('items', [])
        if isinstance(raw_items, str):
            try:
                items_data = json.loads(raw_items)
            except (ValueError, TypeError):
                items_data = []
        else:
            items_data = raw_items

        if not customer_name or not customer_email:
            return error("Customer name and email are required.")
        if not items_data:
            return error("At least one item is required.")

        built = []
        for it in items_data:
            name  = str(it.get('name', '')).strip()
            price = float(it.get('price', 0) or 0)
            qty   = float(it.get('qty', 1) or 1)
            tax   = float(it.get('tax', 0) or 0)
            if not name or price <= 0:
                continue
            sub     = round(price * qty, 2)
            tax_amt = round(sub * tax / 100, 2)
            built.append(InvoiceItem(
                product_id='free', product_name=name, description='',
                hsn_code='', unit='Nos',
                unit_price=price, quantity=qty, tax_rate=tax, discount=0,
                subtotal=sub, tax_amount=tax_amt, total=round(sub + tax_amt, 2),
            ))

        if not built:
            return error("No valid items provided.")

        free_count = Invoice.objects(created_by='anonymous').count()
        inv_number = inv_number_custom or f"INV-{date.today().strftime('%Y%m%d')}-{free_count + 1:03d}"

        grand_total = sum(float(i.total) for i in built)
        tax_total   = sum(float(i.tax_amount) for i in built)
        sub_total   = sum(float(i.subtotal) for i in built)

        invoice = Invoice(
            invoice_number=inv_number,
            customer_id='anonymous',
            customer_name=customer_name,
            customer_email=customer_email,
            customer_address=customer_address,
            customer_gst='',
            invoice_date=datetime.utcnow(),
            due_date=datetime.utcnow(),
            items=built,
            subtotal=sub_total,
            tax_amount=tax_total,
            grand_total=grand_total,
            status='Draft',
            notes=notes,
            terms=terms,
            currency='INR',
            created_by='anonymous',
        ).save()

        # Handle optional logo file upload
        logo_path = ''
        logo_file = request.FILES.get('logo')
        if logo_file and logo_file.size > 0:
            ext = logo_file.name.rsplit('.', 1)[-1].lower() if '.' in logo_file.name else 'png'
            logo_filename = f"free_logo_{uuid.uuid4().hex[:10]}.{ext}"
            logo_dir = os.path.join(settings.MEDIA_ROOT, 'free_logos')
            os.makedirs(logo_dir, exist_ok=True)
            logo_abs = os.path.join(logo_dir, logo_filename)
            with open(logo_abs, 'wb') as f:
                for chunk in logo_file.chunks():
                    f.write(chunk)
            logo_path = f"free_logos/{logo_filename}"

        # Persist seller info so the PDF download endpoint can use it
        seller_data = {
            'name': from_name, 'email': from_email,
            'address': from_address, 'phone': from_phone, 'gst': from_gst,
            'logo_path': logo_path,
            'customer_phone': customer_phone,
            'customer_mobile': customer_mobile, 'customer_fax': customer_fax,
        }
        _save_seller(str(invoice.pk), seller_data)

        if usage:
            usage.count += 1
            usage.save()
        else:
            FreeInvoiceUsage(ip=ip, date_str=today, count=1).save()

        return success({
            'id': str(invoice.pk),
            'invoice_number': invoice.invoice_number,
            'customer_name': invoice.customer_name,
            'customer_email': invoice.customer_email,
            'subtotal': float(sub_total),
            'tax_amount': float(tax_total),
            'grand_total': float(grand_total),
            'invoice_date': invoice.invoice_date.strftime('%d %b %Y'),
            'items': [
                {'name': i.product_name, 'qty': float(i.quantity),
                 'price': float(i.unit_price), 'tax': float(i.tax_rate), 'total': float(i.total)}
                for i in invoice.items
            ],
            'remaining_today': max(0, FREE_DAILY_LIMIT - (usage.count if usage else 1)),
        }, "Free invoice created.", 201)


class FreeInvoicePDFView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, pk):
        invoice = Invoice.objects(pk=pk, created_by='anonymous').first()
        if not invoice:
            return error("Invoice not found.", status=404)
        seller = _load_seller(str(invoice.pk))
        pdf_path = generate_invoice_pdf(invoice, seller=seller)
        invoice.pdf_path = pdf_path
        invoice.save()
        full_path = os.path.join(settings.MEDIA_ROOT, pdf_path)
        f = open(full_path, 'rb')
        response = FileResponse(f, content_type='application/pdf',
                                as_attachment=True,
                                filename=f"{invoice.invoice_number}.pdf")
        response['X-Accel-Buffering'] = 'no'
        return response
