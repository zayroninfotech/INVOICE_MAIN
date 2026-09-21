import mongoengine as me
from datetime import datetime


class AuditLog(me.Document):
    actor_id    = me.StringField(default='system')
    actor_email = me.StringField(default='system')
    action      = me.StringField(required=True)
    detail      = me.StringField(default='')
    ip          = me.StringField(default='')
    created_at  = me.DateTimeField(default=datetime.utcnow)

    meta = {'collection': 'audit_logs', 'ordering': ['-created_at'], 'indexes': ['-created_at']}

    @classmethod
    def log(cls, actor, action, detail='', ip=''):
        cls(
            actor_id=str(actor.id) if hasattr(actor, 'id') else 'system',
            actor_email=getattr(actor, 'email', 'system'),
            action=action,
            detail=detail,
            ip=ip,
        ).save()


class TemplateBlock(me.Document):
    template_id = me.StringField(required=True, unique=True)
    blocked_at  = me.DateTimeField(default=datetime.utcnow)
    blocked_by  = me.StringField(default='')

    meta = {'collection': 'template_blocks'}


class TemplateConfig(me.Document):
    """Per-template admin configuration — overrides the registry's default plan gate."""
    template_id = me.StringField(required=True, unique=True)
    min_plan    = me.StringField(choices=['free', 'plus', 'pro', 'unlimited'], default='free')
    updated_at  = me.DateTimeField(default=datetime.utcnow)
    updated_by  = me.StringField(default='')

    meta = {'collection': 'template_configs'}

    def save(self, *args, **kwargs):
        self.updated_at = datetime.utcnow()
        return super().save(*args, **kwargs)


class SmtpConfig(me.Document):
    """App-wide outgoing-mail settings, editable from the admin panel.

    A singleton (key='default'). Anything left blank falls back to the matching
    Django setting from .env, so an untouched install keeps working off the
    environment alone. Invoices are sent from this one mailbox for every vendor;
    the vendor's own address goes in Reply-To, so replies reach them directly.
    """
    key           = me.StringField(default='default', unique=True)
    host          = me.StringField(default='')
    port          = me.IntField(default=0)
    use_tls       = me.BooleanField(default=True)
    use_ssl       = me.BooleanField(default=False)
    username      = me.StringField(default='')
    password      = me.StringField(default='')
    from_email    = me.StringField(default='')
    enabled       = me.BooleanField(default=True)
    updated_at    = me.DateTimeField(default=datetime.utcnow)
    updated_by    = me.StringField(default='')

    meta = {'collection': 'smtp_config'}

    @classmethod
    def load(cls):
        return cls.objects(key='default').first() or cls(key='default')

    def save(self, *args, **kwargs):
        self.updated_at = datetime.utcnow()
        return super().save(*args, **kwargs)


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
    cin          = me.StringField(default='')
    pan          = me.StringField(default='')
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
