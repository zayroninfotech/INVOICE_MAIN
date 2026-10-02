import json
from django.shortcuts import render, redirect

from .template_registry import template_specs_payload


def _no_store(response):
    # These pages fetch live invoice data via JS on load; a bfcache/browser
    # cache replay would skip that fetch and show a stale pre-edit DOM
    # snapshot (e.g. after Back from a save), which reads as lost data.
    response['Cache-Control'] = 'no-store'
    return response


def _web_media(ctx):
    """print_context hands back MEDIA_ROOT-relative paths (what the PDF printer
    needs); a browser needs them under MEDIA_URL or the logo shows as broken."""
    from django.conf import settings
    for key in ('logo_url', 'sig_url'):
        v = ctx.get(key) or ''
        if v and not v.startswith(('http', 'data:', '/', 'file:')):
            ctx[key] = settings.MEDIA_URL + v
    return ctx


def invoice_list(request):
    return render(request, 'invoices/list.html')


def invoice_create(request):
    return _no_store(render(request, 'invoices/form.html', {
        'action': 'create',
        'template_specs_json': json.dumps(template_specs_payload()),
    }))


def invoice_detail(request, pk):
    return _no_store(render(request, 'invoices/detail.html', {
        'invoice_id': pk,
        'template_specs_json': json.dumps(template_specs_payload()),
    }))


def invoice_edit(request, pk):
    return _no_store(render(request, 'invoices/edit.html', {
        'action': 'edit',
        'invoice_id': pk,
        'template_specs_json': json.dumps(template_specs_payload()),
    }))


def invoice_approve(request, token):
    """The customer's page, reached from the emailed link — no login.

    Server-rendered from the same context the print page uses, so the customer
    approves the document they will actually receive. The share token is the
    only credential, so an unknown one is a flat 404 rather than a hint that
    some other token would work.
    """
    from django.http import Http404
    from .models import Invoice
    from .print_context import build_print_context
    from apps.authentication.models import BusinessProfile

    invoice = Invoice.objects(share_token=token).first() if token else None
    if not invoice:
        raise Http404("Invoice link not found")

    bp = BusinessProfile.objects(user_id=invoice.created_by).first()
    ctx = _web_media(build_print_context(invoice, bp, None,
                                         getattr(invoice, 'template_style', 'classic')))
    from .exact_template import page_html
    ctx['exact_html'] = page_html(ctx, invoice)
    ctx.update({
        'token':           token,
        'approval_status': invoice.approval_status,
        'approval_note':   invoice.approval_note,
        'approval_by':     invoice.approval_by,
        'approval_at':     invoice.approval_at,
        'payment_status':  invoice.payment_status,
        'invoice_number':  invoice.invoice_number,
        'customer_name':   invoice.customer_name,
        'seller_name':     (bp.company_name if bp else '') or invoice.signature_company,
        'seller_logo':     ctx.get('logo_url') or '',
        'pdf_url':         f"/api/invoices/public/{token}/pdf/",
    })
    return _no_store(render(request, 'invoices/approve.html', ctx))


def invoice_templates_admin(request):
    """Superadmin "Add Invoice" page — create templates that appear in Buy
    Invoice. The page itself is a shell; the API enforces the superadmin role."""
    return _no_store(render(request, 'invoices/templates_admin.html', {
        'template_specs_json': json.dumps(template_specs_payload()),
    }))


def invoice_free_generator(request):
    """Redirect to the landing page with the free invoice form auto-opened."""
    return redirect('/?free=1')


def invoice_print(request, pk):
    """The exact page headless Chrome prints into the PDF.

    Same URL, same HTML, same CSS partials as the PDF path — so this can be
    opened in a browser and measured against the live preview instead of
    comparing a PDF to a screenshot and guessing at the difference.
    """
    from .models import Invoice
    from .print_context import build_print_context
    from apps.authentication.models import BusinessProfile

    invoice = Invoice.objects(pk=pk).first()
    if not invoice:
        return redirect('invoice_list_page')
    bp = BusinessProfile.objects(user_id=invoice.created_by).first()
    ctx = _web_media(build_print_context(invoice, bp, None,
                                         getattr(invoice, 'template_style', 'classic')))
    from .exact_template import page_html
    ctx['exact_html'] = page_html(ctx, invoice)
    return _no_store(render(request, 'invoices/print.html', ctx))
