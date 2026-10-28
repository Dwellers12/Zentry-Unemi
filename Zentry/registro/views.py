import json

from django.contrib import messages
from django.db.models import Count, Q
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods, require_POST

from cuentas import roles

from . import servicios
from .forms import ResidenteForm, VehiculoForm
from .models import Camara, Plaza, Residente, Vehiculo


# Residentes (RF-01) ------------------------------------------------------------

def _pantalla_residentes(request, form, editar_form=None, editando=None):
    q = request.GET.get('q', '').strip()
    residentes = Residente.objects.prefetch_related('vehiculos').annotate(n_vehiculos=Count('vehiculos'))
    if q:
        residentes = residentes.filter(
            Q(nombre__icontains=q) | Q(cedula__icontains=q) | Q(departamento__icontains=q)
            | Q(vehiculos__placa__icontains=q)
        ).distinct()
    return render(request, 'registro/residentes.html', {
        'seccion': 'residentes',
        'titulo': 'Residentes',
        'residentes': residentes,
        'total': Residente.objects.count(),
        'q': q,
        'form': form,
        'editar_form': editar_form,
        'editando': editando,
    })


@roles.solo_admin
def residentes(request):
    form = ResidenteForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        residente = form.save()
        messages.success(request, 'Residente registrado')
        if request.POST.get('accion') == 'vehiculo':
            return redirect(f"{reverse('registro:vehiculos')}?residente={residente.pk}#registrar")
        return redirect('registro:residentes')
    return _pantalla_residentes(request, form)


@roles.solo_admin
def residente_editar(request, pk):
    residente = get_object_or_404(Residente, pk=pk)
    editar_form = ResidenteForm(request.POST or None, instance=residente, prefix='editar')
    if request.method == 'POST' and editar_form.is_valid():
        editar_form.save()
        messages.success(request, 'Residente actualizado')
        return redirect('registro:residentes')
    return _pantalla_residentes(request, ResidenteForm(), editar_form, residente)


@roles.solo_admin
@require_POST
def residente_eliminar(request, pk):
    residente = get_object_or_404(Residente, pk=pk)
    residente.delete()  # sus vehículos se eliminan con él (CASCADE)
    messages.success(request, 'Residente eliminado')
    return redirect('registro:residentes')


# Vehículos y plazas (RF-02 a RF-04) -----------------------------------------------

def _pantalla_vehiculos(request, form, editar_form=None, editando=None):
    camaras = list(servicios.camaras_con_plazas())
    sin_camara = list(Plaza.objects.filter(camara__isnull=True))
    return render(request, 'registro/vehiculos.html', {
        'seccion': 'vehiculos',
        'titulo': 'Vehículos y plazas',
        'camaras': camaras,
        'plazas_sin_camara': sin_camara,
        'vehiculos': Vehiculo.objects.select_related('residente', 'plaza'),
        'form': form,
        'editar_form': editar_form,
        'editando': editando,
        # {id_plaza: id_residente}: el JavaScript oculta las plazas que ya son de otro residente.
        'duenos_form': json.dumps(form.duenos_por_plaza()),
        'duenos_editar': json.dumps(editar_form.duenos_por_plaza()) if editar_form else '{}',
    })


@roles.solo_admin
def vehiculos(request):
    inicial = {}
    if request.GET.get('residente', '').isdigit():
        inicial['residente'] = int(request.GET['residente'])
    form = VehiculoForm(request.POST or None, initial=inicial)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Vehículo registrado')
        return redirect('registro:vehiculos')
    return _pantalla_vehiculos(request, form)


@roles.solo_admin
def vehiculo_editar(request, pk):
    vehiculo = get_object_or_404(Vehiculo, pk=pk)
    editar_form = VehiculoForm(request.POST or None, instance=vehiculo, prefix='editar')
    if request.method == 'POST' and editar_form.is_valid():
        editar_form.save()
        messages.success(request, 'Vehículo actualizado')
        return redirect('registro:vehiculos')
    return _pantalla_vehiculos(request, VehiculoForm(), editar_form, vehiculo)


@roles.solo_admin
@require_POST
def vehiculo_eliminar(request, pk):
    vehiculo = get_object_or_404(Vehiculo, pk=pk)
    vehiculo.delete()
    messages.success(request, 'Vehículo eliminado')
    return redirect('registro:vehiculos')


# Cámaras -------------------------------------------------------------------------

@roles.usuario_web
def imagen_camara(request, codigo):
    """Última imagen de la cámara (solo con sesión iniciada, RNF-08)."""
    camara = get_object_or_404(Camara, codigo=codigo)
    ruta = camara.ruta_imagen()
    if not ruta.exists():
        raise Http404('La cámara todavía no envió imágenes')
    respuesta = FileResponse(open(ruta, 'rb'), content_type='image/jpeg')
    # La URL lleva ?t=<hora de la captura>: cada captura nueva es otra URL, así que se puede guardar en caché.
    respuesta['Cache-Control'] = 'private, max-age=300'
    return respuesta


def _plaza_json(plaza):
    return {'id': plaza.pk, 'nombre': plaza.nombre, 'poligono': plaza.poligono,
            'vehiculos': [v.placa for v in plaza.vehiculos.all()]}


@roles.solo_admin
@require_http_methods(['GET', 'PUT', 'POST'])
def api_plazas_camara(request, codigo):
    """
    Editor de plazas (RF-03).
    GET: plazas de la cámara y las sugeridas por el agente.
    PUT/POST {plazas: [{id, nombre, poligono}]}: reemplaza las plazas de la cámara.
    """
    camara = get_object_or_404(Camara, codigo=codigo)
    if request.method == 'GET':
        return JsonResponse({
            'camara': {'codigo': camara.codigo, 'nombre': camara.nombre, 'ancho': camara.ancho,
                       'alto': camara.alto, 'tipo': camara.tipo,
                       'imagen': reverse('registro:imagen_camara', args=[camara.codigo])},
            'plazas': [_plaza_json(p) for p in servicios.plazas_de_camara(camara)],
            'sugeridas': camara.plazas_sugeridas,
            'siguiente_nombre': servicios.siguiente_nombre_libre(),
            'nombres_otras_camaras': list(Plaza.objects.exclude(camara=camara).values_list('nombre', flat=True)),
        })

    try:
        datos = json.loads(request.body or b'{}')
    except ValueError:
        return JsonResponse({'error': 'JSON no válido'}, status=400)
    try:
        guardadas, eliminadas = servicios.guardar_plazas_camara(camara, datos.get('plazas'))
    except servicios.ErrorPlazas as error:
        return JsonResponse({'error': str(error)}, status=400)
    return JsonResponse({
        'ok': True,
        'plazas': [_plaza_json(p) for p in guardadas],
        'eliminadas': eliminadas,
    })
