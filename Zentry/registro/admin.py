from django.contrib import admin

from .models import Camara, Plaza, Residente, Vehiculo


class VehiculoEnLinea(admin.TabularInline):
    model = Vehiculo
    extra = 0


@admin.register(Residente)
class ResidenteAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'cedula', 'departamento', 'telefono')
    search_fields = ('nombre', 'cedula', 'departamento', 'vehiculos__placa')
    inlines = [VehiculoEnLinea]


@admin.register(Vehiculo)
class VehiculoAdmin(admin.ModelAdmin):
    list_display = ('placa', 'marca_modelo', 'color', 'residente', 'plaza')
    list_filter = ('plaza__camara',)
    search_fields = ('placa', 'residente__nombre')


@admin.register(Plaza)
class PlazaAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'camara')
    list_filter = ('camara',)


@admin.register(Camara)
class CamaraAdmin(admin.ModelAdmin):
    list_display = ('codigo', 'nombre', 'tipo', 'url', 'activa', 'ultima_captura')
    list_filter = ('tipo', 'activa')
    readonly_fields = ('ultima_captura', 'ultima_conexion', 'sugeridas_en')
