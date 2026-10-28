from django import forms

from .models import Plaza, Residente, Vehiculo


class ResidenteForm(forms.ModelForm):
    class Meta:
        model = Residente
        fields = ['nombre', 'cedula', 'departamento', 'telefono', 'correo']
        labels = {
            'nombre': 'Nombre completo',
            'cedula': 'Cédula',
            'departamento': 'Departamento o casa',
            'telefono': 'Teléfono',
            'correo': 'Correo (opcional)',
        }
        widgets = {
            'nombre': forms.TextInput(attrs={'placeholder': 'María Torres', 'autocomplete': 'off'}),
            'cedula': forms.TextInput(attrs={'placeholder': '0923456781', 'inputmode': 'numeric', 'maxlength': 10}),
            'departamento': forms.TextInput(attrs={'placeholder': 'A-101'}),
            'telefono': forms.TextInput(attrs={'placeholder': '0987654321', 'inputmode': 'tel'}),
            'correo': forms.EmailInput(attrs={'placeholder': 'nombre@correo.com'}),
        }
        error_messages = {
            'nombre': {'required': 'Ingresa el nombre completo'},
            'cedula': {'required': 'La cédula debe tener 10 dígitos',
                       'unique': 'La cédula ya está registrada'},
            'departamento': {'required': 'Ingresa el departamento o casa'},
            'telefono': {'required': 'Ingresa 10 dígitos que empiecen por 09'},
            'correo': {'invalid': 'Ingresa un correo válido'},
        }

    def clean_nombre(self):
        return ' '.join(self.cleaned_data['nombre'].split())

    def clean_telefono(self):
        return ''.join(self.cleaned_data['telefono'].split())

    def clean_departamento(self):
        return self.cleaned_data['departamento'].strip().upper()


class PlazaChoiceField(forms.ModelChoiceField):
    duenos = {}

    def label_from_instance(self, plaza):
        texto = plaza.nombre
        if plaza.camara_id:
            texto += f' · {plaza.camara.nombre}'
        dueno = self.duenos.get(plaza.pk)
        if dueno:
            texto += f' · de {dueno.nombre}'
        return texto


class VehiculoForm(forms.ModelForm):
    plaza = PlazaChoiceField(
        queryset=Plaza.objects.none(), label='Plaza asignada', empty_label='Seleccionar plaza',
        error_messages={'required': 'Selecciona una plaza', 'invalid_choice': 'Selecciona una plaza'},
    )

    class Meta:
        model = Vehiculo
        fields = ['placa', 'marca_modelo', 'color', 'residente', 'plaza']
        labels = {'placa': 'Placa', 'marca_modelo': 'Marca y modelo', 'color': 'Color', 'residente': 'Residente'}
        widgets = {
            'placa': forms.TextInput(attrs={'placeholder': 'ABC-1234', 'maxlength': 8, 'autocomplete': 'off',
                                            'data-mayusculas': '1'}),
            'marca_modelo': forms.TextInput(attrs={'placeholder': 'Chevrolet D-Max'}),
            'color': forms.TextInput(attrs={'placeholder': 'Azul'}),
        }
        error_messages = {
            'placa': {'required': 'Formato requerido: ABC-1234', 'unique': 'La placa ya está registrada'},
            'marca_modelo': {'required': 'Ingresa la marca y el modelo'},
            'color': {'required': 'Ingresa el color'},
            'residente': {'required': 'Selecciona un residente', 'invalid_choice': 'Selecciona un residente'},
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['residente'].empty_label = 'Seleccionar residente'
        self.fields['residente'].queryset = Residente.objects.order_by('nombre')
        # Dueño actual de cada plaza (para mostrarlo y filtrar el desplegable).
        plazas = list(Plaza.objects.select_related('camara').prefetch_related('vehiculos__residente'))
        excluir = self.instance.pk
        for plaza in plazas:
            otros = [v for v in plaza.vehiculos.all() if v.pk != excluir]
            plaza.dueno = otros[0].residente if otros else None
        self._plazas = {p.pk: p for p in plazas}
        self.fields['plaza'].queryset = Plaza.objects.select_related('camara')
        self.fields['plaza'].duenos = {p.pk: p.dueno for p in plazas if p.dueno}
        self.fields['plaza'].widget.attrs['data-plazas'] = '1'

    def duenos_por_plaza(self):
        """{id_plaza: id_residente} para que el JavaScript oculte plazas de otros residentes."""
        return {pk: p.dueno.pk for pk, p in self._plazas.items() if p.dueno}

    def clean_placa(self):
        return self.cleaned_data['placa'].strip().upper()

    def clean(self):
        datos = super().clean()
        plaza, residente = datos.get('plaza'), datos.get('residente')
        if plaza and residente:
            dueno = getattr(self._plazas.get(plaza.pk), 'dueno', None)
            if dueno and dueno.pk != residente.pk:
                self.add_error('plaza', f'Esta plaza ya está asignada a {dueno.nombre}')
        return datos
