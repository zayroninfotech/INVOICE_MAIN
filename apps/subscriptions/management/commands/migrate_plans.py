from django.core.management.base import BaseCommand
from django.conf import settings
from apps.subscriptions.models import Subscription, PlanSettings


class Command(BaseCommand):
    help = 'Migrate premium subscriptions to unlimited and backfill PlanSettings prices.'

    def handle(self, *args, **options):
        # 1. Migrate premium → unlimited
        premium_subs = Subscription.objects(plan='premium')
        count = premium_subs.count()
        for sub in premium_subs:
            sub.plan = 'unlimited'
            sub.save()
        self.stdout.write(self.style.SUCCESS(
            f'Migrated {count} subscription(s) from premium → unlimited.'
        ))

        # 2. Backfill PlanSettings prices if at defaults
        ps = PlanSettings.get()
        changed = False
        if ps.pro_price == 49900 and 'pro' in settings.PLAN_LIMITS:
            expected = settings.PLAN_LIMITS['pro']['price'] * 100
            if ps.pro_price != expected:
                ps.pro_price = expected
                changed = True
        if ps.unlimited_price == 99900 and 'unlimited' in settings.PLAN_LIMITS:
            expected = settings.PLAN_LIMITS['unlimited']['price'] * 100
            if ps.unlimited_price != expected:
                ps.unlimited_price = expected
                changed = True
        if changed:
            ps.save()
            self.stdout.write(self.style.SUCCESS('Updated PlanSettings with new prices.'))
        else:
            self.stdout.write('PlanSettings prices already set — no change.')
