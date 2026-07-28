from django.shortcuts import render, redirect


def customer_list(request):
    return render(request, 'customers/list.html')


def customer_add(request):
    return redirect('/customers/?new=1')


def customer_edit(request, pk):
    return redirect(f'/customers/?edit={pk}')
