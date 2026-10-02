"""Set the app's outgoing-mail (SMTP) settings from the server terminal.

    python manage.py set_smtp --preset hostinger --user billing@zayroinvoice.com --name "Zayro Invoice"

The password is asked for with hidden input (never pass it on the command
line — it would land in shell history). Writes the same SmtpConfig record the
admin panel edits, so Admin → Email (SMTP) shows the result.
"""
import getpass

from django.core.management.base import BaseCommand, CommandError

PRESETS = {
    'hostinger': ('smtp.hostinger.com', 465, True),
    'gmail':     ('smtp.gmail.com', 465, True),
    'outlook':   ('smtp.office365.com', 587, False),
    'zoho':      ('smtp.zoho.in', 465, True),
}


class Command(BaseCommand):
    help = "Set outgoing email (SMTP) settings. Password is asked for with hidden input."

    def add_arguments(self, parser):
        parser.add_argument('--preset', choices=sorted(PRESETS), help='Fill host/port/SSL for a known provider.')
        parser.add_argument('--host', help='SMTP host, e.g. smtp.hostinger.com')
        parser.add_argument('--port', type=int, help='465 (SSL) or 587 (STARTTLS)')
        parser.add_argument('--user', required=True, help='Mailbox address used to log in')
        parser.add_argument('--name', default='Zayro Invoice', help='Sender display name')
        parser.add_argument('--keep-password', action='store_true', help="Don't change the stored password.")

    def handle(self, *args, **o):
        from apps.authentication.models import SmtpConfig, AuditLog

        host, port, use_ssl = PRESETS.get(o['preset'], (None, None, None))
        host = o['host'] or host
        port = o['port'] or port
        if not host or not port:
            raise CommandError("Give --preset hostinger, or both --host and --port.")
        if use_ssl is None:
            use_ssl = port == 465
        user = o['user'].strip()
        if '@' not in user:
            raise CommandError("--user must be the full mailbox address, e.g. billing@zayroinvoice.com")

        cfg = SmtpConfig.load()
        if not o['keep_password']:
            pw = getpass.getpass(f"Password for {user} (typing is hidden): ").strip()
            if not pw:
                raise CommandError("No password entered — nothing saved.")
            pw2 = getpass.getpass("Type it again to confirm: ").strip()
            if pw != pw2:
                raise CommandError("The two passwords don't match — nothing saved.")
            cfg.password = pw

        cfg.host, cfg.port = host, port
        cfg.use_ssl, cfg.use_tls = use_ssl, not use_ssl
        cfg.username = user
        cfg.from_email = f"{o['name']} <{user}>"
        cfg.enabled = True
        cfg.updated_by = 'set_smtp (server)'
        cfg.save()
        try:
            AuditLog(actor_email='server', action='smtp_config',
                     detail=f'SMTP set from terminal: {host}:{port} as {user}').save()
        except Exception:
            pass

        self.stdout.write(self.style.SUCCESS(
            f"Saved: {host}:{port} {'SSL' if use_ssl else 'STARTTLS'}  user={user}  from={cfg.from_email}"))
        self.stdout.write("Now check it:  venv/bin/python manage.py check_smtp --send-to you@example.com")
