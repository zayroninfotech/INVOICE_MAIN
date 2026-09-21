"""Sending an invoice to the customer by email.

One mailbox sends for every vendor (see SmtpConfig) with the vendor's own
address in Reply-To, so the customer's reply goes to them and not to us.

The message carries three things, which is what the customer needs to act
without downloading anything:

    1. a link to the public approval page (Invoice.share_token)
    2. the first page rendered inline as a PNG, so the invoice is visible in
       the mail client itself
    3. the full PDF as an attachment

Sending is synchronous. The Celery task in tasks.py wraps the same function,
but the view calls it directly: a queued send silently does nothing when no
broker is running, which reads to the vendor as "sent" when nothing left.
"""
import os
import logging
from email.mime.image import MIMEImage

from django.conf import settings
from django.core.mail import EmailMultiAlternatives, get_connection as _dj_connection
from django.utils.html import escape

logger = logging.getLogger(__name__)


# ── connection ──────────────────────────────────────────────────────────────

def smtp_settings():
    """Effective outgoing-mail settings: admin panel first, .env underneath."""
    from apps.authentication.models import SmtpConfig
    try:
        cfg = SmtpConfig.load()
    except Exception:           # DB unreachable — .env alone still works
        cfg = None

    def pick(attr, fallback):
        v = getattr(cfg, attr, None) if cfg else None
        return v if v not in (None, '', 0) else fallback

    return {
        'host':       pick('host', settings.EMAIL_HOST),
        'port':       int(pick('port', settings.EMAIL_PORT)),
        'use_tls':    bool(cfg.use_tls) if cfg else settings.EMAIL_USE_TLS,
        'use_ssl':    bool(cfg.use_ssl) if cfg else False,
        'username':   pick('username', settings.EMAIL_HOST_USER),
        'password':   pick('password', settings.EMAIL_HOST_PASSWORD),
        'from_email': pick('from_email', settings.DEFAULT_FROM_EMAIL),
        'enabled':    bool(cfg.enabled) if cfg else True,
    }


def get_connection(cfg=None):
    cfg = cfg or smtp_settings()
    # Django raises if both are set, and SSL on 587 / STARTTLS on 465 fails with
    # WRONG_VERSION_NUMBER. The port decides for the two standard ones.
    port, tls, ssl = cfg['port'], cfg['use_tls'], cfg['use_ssl']
    if port == 465:
        tls, ssl = False, True
    elif port == 587:
        tls, ssl = True, False
    else:
        tls = tls and not ssl
    return _dj_connection(
        host=cfg['host'], port=port,
        username=cfg['username'], password=cfg['password'],
        use_tls=tls, use_ssl=ssl,
    )


class MailNotConfigured(RuntimeError):
    """Raised instead of handing SMTP obviously-unusable credentials."""


def check_configured(cfg=None):
    cfg = cfg or smtp_settings()
    if not cfg['enabled']:
        raise MailNotConfigured("Outgoing email is switched off in the admin panel.")
    if not cfg['host']:
        raise MailNotConfigured("No SMTP host is configured.")
    # The shipped .env carries a placeholder; sending with it fails deep inside
    # smtplib with an auth error that tells the vendor nothing.
    if not cfg['username'] or cfg['username'].startswith('your@'):
        raise MailNotConfigured(
            "SMTP is not set up yet. An administrator can add the mail account "
            "under Admin Panel → Email (SMTP)."
        )
    return cfg


# ── invoice image ───────────────────────────────────────────────────────────

def invoice_png(pdf_abs_path, dpi=110):
    """First page of the PDF as PNG bytes, or None if it can't be rendered."""
    if not pdf_abs_path or not os.path.exists(pdf_abs_path):
        return None
    try:
        import fitz                          # PyMuPDF
        with fitz.open(pdf_abs_path) as doc:
            if not doc.page_count:
                return None
            return doc[0].get_pixmap(dpi=dpi).tobytes('png')
    except Exception:
        # The mail is still worth sending with just the link and the PDF.
        logger.warning("invoice PNG render failed for %s", pdf_abs_path, exc_info=True)
        return None


# ── message ─────────────────────────────────────────────────────────────────

def approval_url(invoice, base_url=''):
    base = (base_url or getattr(settings, 'SITE_URL', '') or '').rstrip('/')
    return f"{base}/i/{invoice.share_token}/"


def _money(invoice):
    try:
        return f"{invoice.currency} {float(invoice.grand_total):,.2f}"
    except Exception:
        return str(invoice.grand_total)


def _date(d):
    try:
        return d.strftime('%d %b %Y')
    except Exception:
        return ''


def build_invoice_email(invoice, seller_name, reply_to='', to=None, note='',
                        base_url='', pdf_abs_path='', cfg=None):
    """Assemble the message. Separated from sending so it can be tested dry."""
    cfg = cfg or smtp_settings()
    to = to or [invoice.customer_email]
    link = approval_url(invoice, base_url)
    sender = seller_name or 'Your supplier'
    total = _money(invoice)
    due = _date(invoice.due_date)

    subject = f"Invoice {invoice.invoice_number} from {sender}"

    text = [
        f"Dear {invoice.customer_name},",
        "",
        f"{sender} has sent you invoice {invoice.invoice_number} for {total}.",
    ]
    if due:
        text.append(f"Payment is due by {due}.")
    if note:
        text += ["", note]
    text += [
        "",
        "Review the invoice and approve or decline it here:",
        link,
        "",
        "The full invoice is attached as a PDF.",
        "",
        f"Thank you,\n{sender}",
    ]
    text = "\n".join(text)

    note_html = (f'<p style="margin:0 0 16px;color:#334155;font-size:15px;line-height:1.6">'
                 f'{escape(note)}</p>') if note else ''
    due_html = (f'<p style="margin:0 0 4px;color:#64748b;font-size:14px">'
                f'Due by <strong style="color:#0f172a">{escape(due)}</strong></p>') if due else ''

    html = f"""<!DOCTYPE html>
<html><body style="margin:0;padding:24px;background:#f5f4f2;font-family:Arial,Helvetica,sans-serif">
  <div style="max-width:620px;margin:0 auto;background:#fff;border:1px solid #e8e7e5;border-radius:14px;padding:32px">
    <p style="margin:0 0 6px;color:#64748b;font-size:14px">Invoice {escape(invoice.invoice_number)}</p>
    <h1 style="margin:0 0 6px;font-size:26px;color:#0f172a">{escape(total)}</h1>
    {due_html}
    <p style="margin:16px 0 20px;color:#334155;font-size:15px;line-height:1.6">
      Dear {escape(invoice.customer_name)},<br>
      {escape(sender)} has sent you the invoice below.
    </p>
    {note_html}
    <a href="{escape(link)}"
       style="display:inline-block;padding:13px 26px;background:#c1121f;color:#fff;text-decoration:none;
              border-radius:8px;font-weight:700;font-size:15px">Review and approve</a>
    <p style="margin:14px 0 26px;color:#94a3b8;font-size:13px">
      Or open this link: <a href="{escape(link)}" style="color:#c1121f">{escape(link)}</a>
    </p>
    <img src="cid:invoice_preview" alt="Invoice {escape(invoice.invoice_number)}"
         style="width:100%;border:1px solid #e8e7e5;border-radius:10px;display:block">
    <p style="margin:24px 0 0;color:#94a3b8;font-size:13px;border-top:1px solid #e8e7e5;padding-top:16px">
      The full invoice is attached as a PDF. Reply to this email to reach {escape(sender)} directly.
    </p>
  </div>
</body></html>"""

    msg = EmailMultiAlternatives(
        subject=subject, body=text,
        from_email=cfg['from_email'], to=to,
        reply_to=[reply_to] if reply_to else None,
    )
    msg.attach_alternative(html, 'text/html')

    png = invoice_png(pdf_abs_path)
    if png:
        img = MIMEImage(png, 'png')
        img.add_header('Content-ID', '<invoice_preview>')
        img.add_header('Content-Disposition', 'inline',
                       filename=f'{invoice.invoice_number}.png')
        msg.attach(img)
        msg.mixed_subtype = 'related'

    if pdf_abs_path and os.path.exists(pdf_abs_path):
        msg.attach_file(pdf_abs_path)

    return msg


def send_invoice_email(invoice, seller_name='', reply_to='', to=None, note='',
                       base_url='', pdf_abs_path=''):
    """Send now. Raises MailNotConfigured / SMTP errors for the caller to report."""
    cfg = check_configured()
    msg = build_invoice_email(invoice, seller_name, reply_to=reply_to, to=to,
                              note=note, base_url=base_url,
                              pdf_abs_path=pdf_abs_path, cfg=cfg)
    msg.connection = get_connection(cfg)
    msg.send(fail_silently=False)
    return msg.to


def send_test_email(to):
    """Admin-panel 'send a test' — proves the mailbox works before a real send."""
    cfg = check_configured()
    msg = EmailMultiAlternatives(
        subject='Zayro Invoice — SMTP test',
        body='This is a test message. Your outgoing mail settings are working.',
        from_email=cfg['from_email'], to=[to],
        connection=get_connection(cfg),
    )
    msg.send(fail_silently=False)
