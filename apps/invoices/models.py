import mongoengine as me
from datetime import datetime


class InvoiceItem(me.EmbeddedDocument):
    product_id = me.StringField(required=True)
    product_name = me.StringField(required=True)   # snapshot
    description = me.StringField(default='')
    hsn_code = me.StringField(default='')
    unit = me.StringField(default='piece')
    unit_price = me.DecimalField(required=True, precision=2)  # snapshot
    quantity = me.DecimalField(required=True, precision=2, min_value=0)
    tax_rate = me.DecimalField(default=18.0, precision=2)     # snapshot
    discount = me.DecimalField(default=0.0, precision=2)      # % discount on this line
    subtotal = me.DecimalField(precision=2)    # unit_price * quantity
    tax_amount = me.DecimalField(precision=2)
    total = me.DecimalField(precision=2)       # subtotal + tax - discount


class Invoice(me.Document):
    invoice_number = me.StringField(required=True)
    customer_id = me.StringField(required=True)
    customer_name = me.StringField(required=True)   # snapshot
    customer_email = me.EmailField(required=True)   # snapshot
    customer_address = me.StringField(default='')   # snapshot
    customer_gst = me.StringField(default='')       # snapshot
    customer_phone = me.StringField(default='')     # snapshot
    customer_pan = me.StringField(default='')       # snapshot
    customer_cin = me.StringField(default='')       # snapshot
    customer_recipient = me.StringField(default='')  # contact person / ship-to name
    # Per-component GST rates. The form has always collected all three but only
    # posted their sum as each item's tax_rate, so _gst_split() had to fall back
    # to halving tax_amount (IGST always 0). Zero here means "legacy invoice —
    # use the halving fallback"; see _merge_seller() in pdf_generator.py.
    cgst_rate = me.FloatField(default=0)
    sgst_rate = me.FloatField(default=0)
    igst_rate = me.FloatField(default=0)
    invoice_date = me.DateTimeField(required=True)
    due_date = me.DateTimeField(required=True)
    items = me.EmbeddedDocumentListField(InvoiceItem)
    subtotal = me.DecimalField(precision=2, default=0)
    discount_amount = me.DecimalField(precision=2, default=0)
    tax_amount = me.DecimalField(precision=2, default=0)
    grand_total = me.DecimalField(precision=2, default=0)
    status = me.StringField(
        choices=['Draft', 'Sent', 'Paid', 'Partial', 'Overdue', 'Cancelled'],
        default='Draft'
    )
    notes = me.StringField(default='')
    terms = me.StringField(default='Payment due within 30 days.')
    currency = me.StringField(default='INR')
    template_color = me.StringField(default='#F97316')
    template_style = me.StringField(default='classic')   # classic|minimal|modern|professional|bold
    layout_config  = me.DictField(default=dict)          # field-toggle overrides
    signature_image = me.StringField(default='')     # data-URL (base64 PNG) of drawn/uploaded signature
    signatory_name = me.StringField(default='')       # typed signatory name
    signature_company = me.StringField(default='')    # company name override for footer/signature area
    department = me.StringField(default='')           # e.g. "Sales & Procurement Department"
    created_by = me.StringField(required=True)
    pdf_path = me.StringField(default='')
    created_at = me.DateTimeField(default=datetime.utcnow)
    updated_at = me.DateTimeField(default=datetime.utcnow)

    meta = {
        'collection': 'invoices',
        'indexes': [
            {'fields': ['invoice_number', 'created_by'], 'unique': True},
            'customer_id', 'status', 'created_by', 'due_date',
        ],
        'ordering': ['-created_at'],
    }

    def save(self, *args, **kwargs):
        self.updated_at = datetime.utcnow()
        from django.utils import timezone
        now = timezone.now() if self.due_date and self.due_date.tzinfo else datetime.utcnow()
        if self.status in ['Draft', 'Sent'] and self.due_date and self.due_date < now:
            self.status = 'Overdue'
        return super().save(*args, **kwargs)

    def calculate_totals(self):
        subtotal = 0
        tax_total = 0
        for item in self.items:
            line_subtotal = float(item.unit_price) * float(item.quantity)
            line_discount = line_subtotal * float(item.discount) / 100
            line_after_discount = line_subtotal - line_discount
            line_tax = line_after_discount * float(item.tax_rate) / 100
            item.subtotal = round(line_subtotal, 2)
            item.tax_amount = round(line_tax, 2)
            item.total = round(line_after_discount + line_tax, 2)
            subtotal += line_after_discount
            tax_total += line_tax
        self.subtotal = round(subtotal, 2)
        self.tax_amount = round(tax_total, 2)
        self.grand_total = round(subtotal + tax_total, 2)
