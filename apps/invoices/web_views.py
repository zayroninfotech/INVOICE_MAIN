from django.shortcuts import render, redirect


def invoice_list(request):
    return render(request, 'invoices/list.html')


def invoice_create(request):
    return render(request, 'invoices/form.html', {'action': 'create'})


def invoice_detail(request, pk):
    return render(request, 'invoices/detail.html', {'invoice_id': pk})


def invoice_edit(request, pk):
    return redirect(f'/invoices/?edit={pk}')
