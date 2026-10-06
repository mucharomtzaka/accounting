"""Form registrasi company + owner (PRD 5).
"""
from django import forms
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError

from .models import Company, Currency, User


class RegisterCompanyForm(forms.Form):
    """Daftar company baru + user owner untuk company tersebut."""

    company_code = forms.CharField(
        max_length=20, label='Kode Company',
        help_text='Kode unik, mis. ABC. Hanya huruf/angka/ strip/ underscore.',
    )
    legal_name = forms.CharField(max_length=255, label='Nama Legal')
    display_name = forms.CharField(max_length=150, label='Nama Tampil')
    npwp = forms.CharField(max_length=30, required=False, label='NPWP')
    base_currency = forms.ChoiceField(label='Mata Uang', choices=(), initial='IDR')

    first_name = forms.CharField(max_length=150, label='Nama Depan')
    last_name = forms.CharField(max_length=150, required=False, label='Nama Belakang')
    email = forms.EmailField(label='Email')
    username = forms.CharField(max_length=150, label='Username')
    password1 = forms.CharField(
        label='Password', widget=forms.PasswordInput, strip=False,
    )
    password2 = forms.CharField(
        label='Ulangi Password', widget=forms.PasswordInput, strip=False,
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['base_currency'].choices = [
            (c.code, f'{c.code} - {c.name}') for c in Currency.objects.filter(is_active=True)
        ]
        if not self.fields['base_currency'].choices:
            self.fields['base_currency'].choices = [('IDR', 'IDR - Rupiah')]

    def clean_company_code(self):
        code = self.cleaned_data['company_code'].strip()
        if not code.replace('-', '').replace('_', '').isalnum():
            raise ValidationError('Kode company hanya boleh huruf, angka, strip, atau underscore.')
        if Company.objects.filter(company_code__iexact=code).exists():
            raise ValidationError('Kode company sudah dipakai.')
        return code.upper()

    def clean_username(self):
        username = self.cleaned_data['username'].strip()
        if User.objects.filter(username__iexact=username).exists():
            raise ValidationError('Username sudah dipakai.')
        return username

    def clean_email(self):
        email = self.cleaned_data['email'].strip()
        if User.objects.filter(email__iexact=email).exists():
            raise ValidationError('Email sudah dipakai.')
        return email

    def clean(self):
        cleaned = super().clean()
        p1, p2 = cleaned.get('password1'), cleaned.get('password2')
        if p1 and p2 and p1 != p2:
            self.add_error('password2', 'Password tidak sama.')
        elif p1:
            try:
                validate_password(p1)
            except ValidationError as exc:
                self.add_error('password1', exc)
        return cleaned