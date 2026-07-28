from config.celery import app
from django.core.mail import EmailMessage
from django.conf import settings
import os


@app.task(bind=True, max_retries=3)
def send_invoice_email_task(self, invoice_id: str, recipient_email: str, pdf_path: str):
    try:
        from apps.invoices.models import Invoice
        invoice = Invoice.objects(pk=invoice_id).first()
        if not invoice:
            return {'error': 'Invoice not found'}

        subject = f"Invoice {invoice.invoice_number} from Invoice System"
        body = (
            f"Dear {invoice.customer_name},\n\n"
            f"Please find attached invoice {invoice.invoice_number} "
            f"for {invoice.currency} {float(invoice.grand_total):,.2f}.\n\n"
            f"Due Date: {invoice.due_date.strftime('%d %b %Y')}\n\n"
            f"Thank you for your business.\n\nBest regards,\nInvoice System"
        )
        email = EmailMessage(subject=subject, body=body,
                             from_email=settings.DEFAULT_FROM_EMAIL,
                             to=[recipient_email])
        full_path = os.path.join(settings.MEDIA_ROOT, pdf_path)
        if os.path.exists(full_path):
            email.attach_file(full_path)
        email.send()
        invoice.status = 'Sent'
        invoice.save()
        return {'success': True, 'invoice_number': invoice.invoice_number}
    except Exception as exc:
        raise self.retry(exc=exc, countdown=60)
