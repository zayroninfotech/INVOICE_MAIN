from django.shortcuts import render, redirect


def product_list(request):
    return render(request, 'products/list.html')


def product_add(request):
    return redirect('/products/?new=1')


def product_edit(request, pk):
    return redirect(f'/products/?edit={pk}')
