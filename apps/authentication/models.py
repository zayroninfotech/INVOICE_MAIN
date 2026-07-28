import mongoengine as me
from datetime import datetime


class BusinessProfile(me.Document):
    user_id      = me.StringField(required=True, unique=True)
    company_name = me.StringField(default='')
    logo_path    = me.StringField(default='')   # relative to MEDIA_ROOT
    address      = me.StringField(default='')
    city         = me.StringField(default='')
    state        = me.StringField(default='')
    pincode      = me.StringField(default='')
    phone        = me.StringField(default='')
    email        = me.StringField(default='')
    gst          = me.StringField(default='')
    website      = me.StringField(default='')
    updated_at   = me.DateTimeField(default=datetime.utcnow)

    meta = {'collection': 'business_profiles', 'indexes': ['user_id']}

    def save(self, *args, **kwargs):
        self.updated_at = datetime.utcnow()
        return super().save(*args, **kwargs)


class User(me.Document):
    username = me.StringField(required=True, unique=True, max_length=150)
    email = me.EmailField(required=True, unique=True)
    password = me.StringField(required=True)
    role = me.StringField(choices=['superadmin', 'admin', 'user'], default='user')
    is_active = me.BooleanField(default=True)
    created_at = me.DateTimeField(default=datetime.utcnow)
    updated_at = me.DateTimeField(default=datetime.utcnow)

    meta = {
        'collection': 'users',
        'indexes': ['email', 'username'],
    }

    @property
    def is_authenticated(self):
        return True

    def save(self, *args, **kwargs):
        self.updated_at = datetime.utcnow()
        return super().save(*args, **kwargs)
