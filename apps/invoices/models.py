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
    invoice_number = me.StringField(required=True, unique=True)
    customer_id = me.StringField(required=True)
    customer_name = me.StringField(required=True)   # snapshot
    customer_email = me.EmailField(required=True)   # snapshot
    customer_address = me.StringField(default='')   # snapshot
    customer_gst = me.StringField(default='')       # snapshot
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
    created_by = me.StringField(required=True)
    pdf_path = me.StringField(default='')
    created_at = me.DateTimeField(default=datetime.utcnow)
    updated_at = me.DateTimeField(default=datetime.utcnow)

    meta = {
        'collection': 'invoices',
        'indexes': ['invoice_number', 'customer_id', 'status', 'created_by', 'due_date'],
        'ordering': ['-created_at'],
    }

    def save(self, *args, **kwargs):
        self.updated_at = datetime.utcnow()
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
