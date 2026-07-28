from django.core.management.base import BaseCommand
from django.contrib.auth.hashers import make_password
from apps.authentication.models import User


class Command(BaseCommand):
    help = 'Create the default superadmin user'

    def handle(self, *args, **options):
        if User.objects(username='vamsi').first():
            self.stdout.write(self.style.WARNING('Superadmin "vamsi" already exists.'))
            return
        User(
            username='vamsi',
            email='vamsi@invoicems.com',
            password=make_password('zayron@2026'),
            role='superadmin',
            is_active=True,
        ).save()
        self.stdout.write(self.style.SUCCESS('Superadmin "vamsi" created successfully.'))
