"""Background wrapper around the invoice emailer.

The web view sends synchronously (see emailer.send_invoice_email) so a vendor
finds out immediately whether the mail left. This task exists for callers that
do have a broker running — it shares the same builder, so the message the
customer receives is identical either way.
"""
import os

from django.conf import settings

from config.celery import app


@app.task(bind=True, max_retries=3)
def send_invoice_email_task(self, invoice_id: str, recipient_email: str = '',
                            pdf_path: str = '', note: str = '', base_url: str = ''):
    from apps.authentication.models import BusinessProfile
    from apps.invoices.models import Invoice
    from apps.invoices.emailer import send_invoice_email
    from apps.invoices.pdf_generator import generate_invoice_pdf

    invoice = Invoice.objects(pk=invoice_id).first()
    if not invoice:
        return {'error': 'Invoice not found'}

    bp = BusinessProfile.objects(user_id=invoice.created_by).first()
    if not pdf_path:
        pdf_path = generate_invoice_pdf(invoice, business_profile=bp)
        invoice.pdf_path = pdf_path
    invoice.ensure_share_token()
    invoice.save()

    try:
        send_invoice_email(
            invoice,
            seller_name=(bp.company_name if bp else '') or invoice.signature_company,
            reply_to=(bp.email if bp else '') or '',
            to=[recipient_email or invoice.customer_email],
            note=note,
            base_url=base_url or getattr(settings, 'SITE_URL', ''),
            pdf_abs_path=os.path.join(settings.MEDIA_ROOT, pdf_path),
        )
    except Exception as exc:
        raise self.retry(exc=exc, countdown=60)

    if invoice.status == 'Draft':
        invoice.status = 'Sent'
        invoice.save()
    return {'success': True, 'invoice_number': invoice.invoice_number}
