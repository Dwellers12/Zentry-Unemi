from django.contrib import admin

from .models import EstadoPlaza, Lectura


@admin.register(EstadoPlaza)
class EstadoPlazaAdmin(admin.ModelAdmin):
    list_display = ('plaza', 'ocupada', 'placa', 'confianza', 'resultado', 'actualizado', 'visto')
    list_filter = ('resultado', 'plaza__camara')
    readonly_fields = [f.name for f in EstadoPlaza._meta.fields]

    def has_add_permission(self, request):
        return False


@admin.register(Lectura)
class LecturaAdmin(admin.ModelAdmin):
    list_display = ('capturada_en', 'plaza_nombre', 'tipo', 'placa', 'placa_anterior', 'confianza', 'resultado')
    list_filter = ('tipo', 'resultado', 'camara')
    search_fields = ('placa', 'placa_anterior', 'plaza_nombre')
    readonly_fields = [f.name for f in Lectura._meta.fields]

    def has_add_permission(self, request):
        return False
