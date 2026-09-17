import json
from django.shortcuts import render, redirect

from .template_registry import template_specs_payload


def _no_store(response):
    # These pages fetch live invoice data via JS on load; a bfcache/browser
    # cache replay would skip that fetch and show a stale pre-edit DOM
    # snapshot (e.g. after Back from a save), which reads as lost data.
    response['Cache-Control'] = 'no-store'
    return response


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
    ctx = build_print_context(invoice, bp, None,
                              getattr(invoice, 'template_style', 'classic'))
    return _no_store(render(request, 'invoices/print.html', ctx))
