import mongoengine as me
from datetime import datetime


class Product(me.Document):
    name = me.StringField(required=True, max_length=200)
    description = me.StringField(default='')
    price = me.DecimalField(required=True, precision=2, min_value=0)
    tax_rate = me.DecimalField(default=18.0, precision=2)  # GST %
    unit = me.StringField(max_length=50, default='piece')
    category = me.StringField(max_length=100, default='General')
    hsn_code = me.StringField(max_length=20, default='')  # HSN for GST
    created_by = me.StringField(required=True)
    is_active = me.BooleanField(default=True)
    created_at = me.DateTimeField(default=datetime.utcnow)
    updated_at = me.DateTimeField(default=datetime.utcnow)

    meta = {
        'collection': 'products',
        'indexes': ['name', 'category', 'created_by'],
        'ordering': ['-created_at'],
    }

    def save(self, *args, **kwargs):
        self.updated_at = datetime.utcnow()
        return super().save(*args, **kwargs)
