import mongoengine as me
from datetime import datetime, date


class Subscription(me.Document):
    user_id = me.StringField(required=True, unique=True)
    plan = me.StringField(choices=['free', 'plus', 'pro', 'unlimited', 'premium'], default='free')
    status = me.StringField(choices=['active', 'expired', 'cancelled'], default='active')
    start_date = me.DateTimeField(default=datetime.utcnow)
    end_date = me.DateTimeField()  # None = free (no expiry gate)
    # Usage counters
    invoices_used_today = me.IntField(default=0)
    invoices_used_month = me.IntField(default=0)
    last_daily_reset = me.StringField(default='')   # YYYY-MM-DD
    last_monthly_reset = me.StringField(default='') # YYYY-MM
    # Payment info
    razorpay_payment_id = me.StringField(default='')
    razorpay_order_id = me.StringField(default='')
    amount_paid = me.DecimalField(default=0, precision=2)
    created_at = me.DateTimeField(default=datetime.utcnow)
    updated_at = me.DateTimeField(default=datetime.utcnow)

    meta = {
        'collection': 'subscriptions',
        'indexes': ['user_id'],
    }

    def _reset_if_needed(self):
        today = date.today()
        today_str = today.strftime('%Y-%m-%d')
        month_str = today.strftime('%Y-%m')
        changed = False
        if self.last_daily_reset != today_str:
            self.invoices_used_today = 0
            self.last_daily_reset = today_str
            changed = True
        if self.last_monthly_reset != month_str:
            self.invoices_used_month = 0
            self.last_monthly_reset = month_str
            changed = True
        return changed

    def can_create_invoice(self):
        """Returns (True, None) or (False, message)."""
        from django.conf import settings
        # Superadmin bypass handled in views
        self._reset_if_needed()
        limits = settings.PLAN_LIMITS.get(self.plan, settings.PLAN_LIMITS['free'])
        daily = limits.get('invoices_per_day')
        monthly = limits.get('invoices_per_month')
        if daily is not None and self.invoices_used_today >= daily:
            return False, f"Daily limit of {daily} invoices reached on your {self.plan.capitalize()} plan. Upgrade to create more."
        if monthly is not None and self.invoices_used_month >= monthly:
            return False, f"Monthly limit of {monthly} invoices reached on your {self.plan.capitalize()} plan. Upgrade to create more."
        return True, None

    def increment_usage(self):
        self._reset_if_needed()
        self.invoices_used_today += 1
        self.invoices_used_month += 1
        self.updated_at = datetime.utcnow()
        self.save()

    def daily_limit(self):
        from django.conf import settings
        return settings.PLAN_LIMITS.get(self.plan, {}).get('invoices_per_day')

    def monthly_limit(self):
        from django.conf import settings
        return settings.PLAN_LIMITS.get(self.plan, {}).get('invoices_per_month')

    def reset_usage_counters(self):
        today = date.today()
        self.invoices_used_today = 0
        self.invoices_used_month = 0
        self.last_daily_reset = today.strftime('%Y-%m-%d')
        self.last_monthly_reset = today.strftime('%Y-%m')
        self.save()

    def save(self, *args, **kwargs):
        self.updated_at = datetime.utcnow()
        return super().save(*args, **kwargs)


class PlanSettings(me.Document):
    """Superadmin-controlled payment gateway + pricing config."""
    key = me.StringField(required=True, unique=True, default='default')
    razorpay_key_id = me.StringField(default='')
    razorpay_key_secret = me.StringField(default='')
    plus_price = me.IntField(default=19900)      # in paise (₹199)
    pro_price = me.IntField(default=49900)       # in paise (₹499)
    unlimited_price = me.IntField(default=99900) # in paise (₹999)
    premium_price = me.IntField(default=229900)  # deprecated — kept for safe doc reads
    is_payments_enabled = me.BooleanField(default=False)
    bank_name = me.StringField(default='')
    bank_account = me.StringField(default='')
    bank_ifsc = me.StringField(default='')
    bank_holder = me.StringField(default='')
    updated_at = me.DateTimeField(default=datetime.utcnow)

    meta = {'collection': 'plan_settings', 'indexes': ['key']}

    @classmethod
    def get(cls):
        obj = cls.objects(key='default').first()
        if not obj:
            obj = cls(key='default').save()
        return obj

    def save(self, *args, **kwargs):
        self.updated_at = datetime.utcnow()
        return super().save(*args, **kwargs)
