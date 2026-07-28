import mongoengine as me
from datetime import datetime


class Customer(me.Document):
    customer_name = me.StringField(required=True, max_length=200)
    company = me.StringField(max_length=200, default='')
    email = me.EmailField(required=True)
    phone = me.StringField(max_length=20, default='')
    address = me.StringField(default='')
    city = me.StringField(max_length=100, default='')
    state = me.StringField(max_length=100, default='')
    pincode = me.StringField(max_length=10, default='')
    gst_number = me.StringField(max_length=20, default='')
    created_by = me.StringField(required=True)  # user id
    is_active = me.BooleanField(default=True)
    created_at = me.DateTimeField(default=datetime.utcnow)
    updated_at = me.DateTimeField(default=datetime.utcnow)

    meta = {
        'collection': 'customers',
        'indexes': ['email', 'customer_name', 'created_by'],
        'ordering': ['-created_at'],
    }

    def save(self, *args, **kwargs):
        self.updated_at = datetime.utcnow()
        return super().save(*args, **kwargs)
