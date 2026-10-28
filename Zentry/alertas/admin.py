from django.contrib import admin

from .models import Alerta, EventoAlerta


class EventoEnLinea(admin.TabularInline):
    model = EventoAlerta
    extra = 0
    readonly_fields = ('tipo', 'texto', 'autor', 'usuario', 'creado_en')
    can_delete = False


@admin.register(Alerta)
class AlertaAdmin(admin.ModelAdmin):
    list_display = ('creada_en', 'plaza_nombre', 'placa', 'resultado', 'estado', 'resolucion_automatica')
    list_filter = ('estado', 'resultado', 'camara')
    search_fields = ('placa', 'plaza_nombre', 'resolucion')
    readonly_fields = ('lectura', 'evidencia', 'evidencia_recorte', 'vehiculo_caja', 'creada_en', 'resuelta_en')
    exclude = ('vista_por',)
    inlines = [EventoEnLinea]
