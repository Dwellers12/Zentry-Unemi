from django import forms
from django.contrib.auth import authenticate, password_validation
from django.contrib.auth.models import User

from . import roles


class IniciarSesionForm(forms.Form):
    usuario = forms.CharField(label='Usuario', max_length=150, required=False)
    contrasena = forms.CharField(label='Contraseña', required=False, strip=False,
                                 widget=forms.PasswordInput(render_value=False))

    def __init__(self, request=None, *args, **kwargs):
        self.request = request
        self.usuario_autenticado = None
        super().__init__(*args, **kwargs)

    def clean(self):
        datos = super().clean()
        nombre = (datos.get('usuario') or '').strip()
        contrasena = datos.get('contrasena') or ''
        if not nombre or not contrasena:
            raise forms.ValidationError('Ingresa tu usuario y contraseña')
        usuario = authenticate(self.request, username=nombre, password=contrasena)
        if usuario is None:
            raise forms.ValidationError('Usuario o contraseña incorrectos')
        if not roles.es_usuario_web(usuario):
            raise forms.ValidationError('Esta cuenta no tiene acceso a la web de Zentry')
        self.usuario_autenticado = usuario
        return datos


class CrearCuentaForm(forms.Form):
    usuario = forms.CharField(label='Usuario', max_length=150)
    contrasena = forms.CharField(label='Contraseña', strip=False, widget=forms.PasswordInput)
    confirmar = forms.CharField(label='Confirmar contraseña', strip=False, widget=forms.PasswordInput)
    rol = forms.ChoiceField(label='Rol', choices=[(r, r) for r in roles.ROLES_WEB])

    def clean_usuario(self):
        nombre = self.cleaned_data['usuario'].strip()
        if len(nombre) < 4:
            raise forms.ValidationError('El usuario debe tener al menos 4 caracteres')
        if not all(c.isalnum() or c in '._-' for c in nombre):
            raise forms.ValidationError('Usa solo letras, números, punto, guion o guion bajo')
        if User.objects.filter(username__iexact=nombre).exists():
            raise forms.ValidationError('Este usuario ya existe')
        return nombre

    def clean(self):
        datos = super().clean()
        contrasena = datos.get('contrasena') or ''
        if len(contrasena) < 8:
            self.add_error('contrasena', 'La contraseña debe tener al menos 8 caracteres')
        else:
            try:
                password_validation.validate_password(contrasena, User(username=datos.get('usuario', '')))
            except forms.ValidationError as error:
                self.add_error('contrasena', error)
        if datos.get('confirmar') != contrasena:
            self.add_error('confirmar', 'Las contraseñas no coinciden')
        return datos
