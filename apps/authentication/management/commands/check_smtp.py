"""Diagnose outgoing mail step by step — run on the server:

    python manage.py check_smtp                      # check settings + login
    python manage.py check_smtp --send-to you@x.com  # ...and send a real test

Each step prints OK/FAIL and the run stops at the first failure with what to
fix, so "authentication failed" stops being a guess between wrong password,
stale saved settings, wrong port and a blocked firewall.
The password is never printed — only whether one is stored and its length.
"""
import smtplib
import socket
import ssl

from django.conf import settings
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Check outgoing email (SMTP) settings, connection and login, step by step."

    def add_arguments(self, parser):
        parser.add_argument('--send-to', default='', help='Also send a real test email to this address.')

    # ── output helpers ───────────────────────────────────────────────────────
    def ok(self, msg):
        self.stdout.write(self.style.SUCCESS(f"  OK    {msg}"))

    def warn(self, msg):
        self.stdout.write(self.style.WARNING(f"  WARN  {msg}"))

    def fail(self, msg, fix):
        self.stdout.write(self.style.ERROR(f"  FAIL  {msg}"))
        self.stdout.write(f"        → {fix}")
        self.stdout.write(self.style.ERROR("\nStopped at the first failure. Fix that, then run this again."))

    def head(self, n, title):
        self.stdout.write(self.style.MIGRATE_HEADING(f"\n[{n}] {title}"))

    # ── checks ───────────────────────────────────────────────────────────────
    def handle(self, *args, **opts):
        from apps.authentication.models import SmtpConfig
        from apps.invoices.emailer import smtp_settings

        # 1. What is saved, and what will actually be used
        self.head(1, "Settings (Admin → Email (SMTP), with .env underneath)")
        try:
            saved = SmtpConfig.load()
        except Exception as exc:
            return self.fail(f"Could not read SMTP settings from the database: {exc}",
                             "Run: python manage.py check_mongo")
        eff = smtp_settings()
        pw = eff['password'] or ''
        src = lambda attr: 'admin panel' if getattr(saved, attr, None) not in (None, '', 0) else '.env'
        self.stdout.write(f"        host      {eff['host']:<34} (from {src('host')})")
        self.stdout.write(f"        port      {str(eff['port']):<34} (from {src('port')})")
        self.stdout.write(f"        SSL/TLS   ssl={eff['use_ssl']} starttls={eff['use_tls']}")
        self.stdout.write(f"        username  {eff['username']:<34} (from {src('username')})")
        self.stdout.write(f"        password  {'stored, ' + str(len(pw)) + ' characters' if pw else 'NOT SET':<34} (from {src('password')})")
        self.stdout.write(f"        from      {eff['from_email']}")
        self.stdout.write(f"        enabled   {eff['enabled']}")
        self.stdout.write(f"        last saved in admin panel: {getattr(saved, 'updated_at', '—')}")

        if not eff['enabled']:
            return self.fail("Sending is switched off.", "Tick “Allow invoices to be emailed” and save.")
        if not eff['host'] or not eff['username']:
            return self.fail("Host or username is empty.", "Fill both in Admin → Email (SMTP) and save.")
        if not pw:
            return self.fail("No password stored.", "Type the mailbox password in Admin → Email (SMTP) and save.")
        if pw != pw.strip():
            return self.fail("The stored password starts or ends with a space.",
                             "Re-type the password (no spaces) and save.")
        user_dom = eff['username'].rsplit('@', 1)[-1].lower()
        from_addr = eff['from_email'].rsplit('<', 1)[-1].rstrip('>').strip().lower()
        if 'gmail.com' in eff['host'] and user_dom not in ('gmail.com', 'googlemail.com'):
            self.warn(f"Host is Gmail but the username is @{user_dom}.")
        if 'hostinger' in eff['host'] and user_dom in ('gmail.com', 'googlemail.com'):
            self.warn("Host is Hostinger but the username is a Gmail address.")
        if from_addr and from_addr != eff['username'].lower():
            self.warn(f"From address ({from_addr}) differs from the username — most servers (Hostinger, Gmail) "
                      "reject that. Set From to the same mailbox.")
        len_like_gmail_app = len(pw.replace(' ', '')) == 16 and pw.replace(' ', '').isalpha()
        if 'gmail' not in eff['host'] and len_like_gmail_app:
            self.warn("The stored password looks like a Gmail app password (16 letters), not a mailbox password.")
        self.ok("settings look complete")

        # 2. DNS
        host, port = eff['host'], int(eff['port'])
        self.head(2, f"DNS: resolve {host}")
        try:
            ip = socket.gethostbyname(host)
            self.ok(f"{host} → {ip}")
        except Exception as exc:
            return self.fail(f"Cannot resolve {host}: {exc}", "Check the host name for typos (e.g. smtp.hostinger.com).")

        # 3. TCP reachability
        self.head(3, f"Network: connect to {host}:{port}")
        try:
            socket.create_connection((host, port), timeout=10).close()
            self.ok(f"port {port} is reachable")
        except Exception as exc:
            return self.fail(f"Cannot connect to {host}:{port}: {exc}",
                             "The server's firewall or hosting provider may block this port. "
                             "Try 465 (SSL) or 587 (STARTTLS), or ask the VPS provider to open outbound SMTP.")

        # 4. Encryption handshake, exactly as the app will do it
        use_ssl = port == 465 or (eff['use_ssl'] and port != 587)
        use_tls = port == 587 or (eff['use_tls'] and not use_ssl)
        self.head(4, f"Secure connection ({'SSL' if use_ssl else 'STARTTLS' if use_tls else 'none'})")
        try:
            ctx = ssl.create_default_context()
            if use_ssl:
                server = smtplib.SMTP_SSL(host, port, timeout=20, context=ctx)
            else:
                server = smtplib.SMTP(host, port, timeout=20)
                server.ehlo()
                if use_tls:
                    server.starttls(context=ctx)
                    server.ehlo()
            self.ok("secure connection established")
        except Exception as exc:
            return self.fail(f"{type(exc).__name__}: {exc}",
                             "Use port 465 with “Use SSL”, or 587 with “Use STARTTLS” — not the other way round.")

        # 5. Login
        self.head(5, f"Login as {eff['username']}")
        try:
            server.login(eff['username'], pw)
            self.ok("the mail server accepted the username and password")
        except smtplib.SMTPAuthenticationError as exc:
            server.close()
            return self.fail(f"Login rejected: {exc.smtp_code} {exc.smtp_error!r}",
                             "Wrong username or password for this server. Check you can log in to webmail "
                             "(Hostinger: mail.hostinger.com) with exactly this address and password, then "
                             "re-type the password in Admin → Email (SMTP) and click Save.")
        except Exception as exc:
            server.close()
            return self.fail(f"{type(exc).__name__}: {exc}", "See the error above.")

        # 6. Optional real send
        if opts['send_to']:
            self.head(6, f"Send a test email to {opts['send_to']}")
            try:
                server.sendmail(from_addr or eff['username'], [opts['send_to']],
                                f"From: {eff['from_email']}\r\nTo: {opts['send_to']}\r\n"
                                f"Subject: Zayro Invoice SMTP check\r\n\r\n"
                                f"This is a test from `manage.py check_smtp`. Outgoing mail works.\r\n")
                self.ok("accepted by the mail server — check the inbox (and spam)")
            except Exception as exc:
                server.close()
                return self.fail(f"{type(exc).__name__}: {exc}",
                                 "If it mentions the sender, set From address to the same mailbox as the username.")
        try:
            server.quit()
        except Exception:
            pass
        self.stdout.write(self.style.SUCCESS("\nAll checks passed — invoices can be emailed."))
