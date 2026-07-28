import mongoengine as me
from datetime import datetime


class Payment(me.Document):
    invoice_id = me.StringField(required=True)
    invoice_number = me.StringField(required=True)
    customer_name = me.StringField(required=True)
    amount = me.DecimalField(required=True, precision=2, min_value=0.01)
    payment_date = me.DateTimeField(required=True)
    payment_method = me.StringField(
        choices=['Cash', 'Bank Transfer', 'UPI', 'Cheque', 'Card', 'Other'],
        default='Bank Transfer'
    )
    reference_number = me.StringField(default='')
    notes = me.StringField(default='')
    created_by = me.StringField(required=True)
    created_at = me.DateTimeField(default=datetime.utcnow)

    meta = {
        'collection': 'payments',
        'indexes': ['invoice_id', 'created_by', 'payment_date'],
        'ordering': ['-created_at'],
    }
