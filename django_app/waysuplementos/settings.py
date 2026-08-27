"""
Django settings for waysuplementos project.
"""

from pathlib import Path

from decouple import Csv, config
from django.contrib.messages import constants as message_constants

BASE_DIR = Path(__file__).resolve().parent.parent

# SECURITY WARNING: keep the secret key used in production secret!
# Em dev, cai no valor padrão abaixo se DJANGO_SECRET_KEY não estiver no .env.
# Em produção, defina DJANGO_SECRET_KEY com um valor forte (ex: gerado com
# `python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"`).
SECRET_KEY = config("DJANGO_SECRET_KEY", default="django-insecure-troque-isso-em-producao")

DEBUG = config("DJANGO_DEBUG", default=True, cast=bool)

ALLOWED_HOSTS = config("DJANGO_ALLOWED_HOSTS", default="localhost,127.0.0.1", cast=Csv())

# Render injeta RENDER_EXTERNAL_HOSTNAME automaticamente em todo serviço — isso
# libera esse host sem precisar saber o nome do serviço de antemão nem editar
# DJANGO_ALLOWED_HOSTS manualmente a cada deploy.
RENDER_EXTERNAL_HOSTNAME = config("RENDER_EXTERNAL_HOSTNAME", default="")
if RENDER_EXTERNAL_HOSTNAME:
    ALLOWED_HOSTS.append(RENDER_EXTERNAL_HOSTNAME)

# O Render termina o HTTPS na borda e encaminha pra cá como HTTP simples com
# esse header — sem isso o Django acha que a conexão não é segura e alguns
# recursos (CSRF em POST, redirects) se comportam errado atrás do proxy.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

CSRF_TRUSTED_ORIGINS = config("DJANGO_CSRF_TRUSTED_ORIGINS", default="", cast=Csv())
if RENDER_EXTERNAL_HOSTNAME:
    CSRF_TRUSTED_ORIGINS.append(f"https://{RENDER_EXTERNAL_HOSTNAME}")


# Application definition

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'estoque',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',  # serve estáticos (CSS/JS do admin) sem precisar de Nginx/CDN
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'waysuplementos.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'estoque.context_processors.perfil_contexto',
            ],
        },
    },
]

WSGI_APPLICATION = 'waysuplementos.wsgi.application'


# Database — Postgres do Supabase (reaproveita produtos/lojas/lotes/movimentacoes
# já cadastrados por uma versão anterior deste projeto). Credenciais vêm do
# .env (nunca hardcode aqui) — ver .env.example.
#
# Aceita DUAS formas (a primeira que existir no .env vence):
#   1. DATABASE_URL — a "Connection string" que o próprio Supabase mostra
#      (Project Settings > Database > Connection string). Mais simples.
#   2. SUPABASE_DB_NAME/USER/PASSWORD/HOST/PORT separados (Connection parameters).
DATABASE_URL = config('DATABASE_URL', default='')
if DATABASE_URL:
    import dj_database_url
    DATABASES = {'default': dj_database_url.parse(DATABASE_URL, conn_max_age=600)}
else:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.postgresql',
            'NAME': config('SUPABASE_DB_NAME', default='postgres'),
            'USER': config('SUPABASE_DB_USER', default='postgres'),
            'PASSWORD': config('SUPABASE_DB_PASSWORD', default=''),
            'HOST': config('SUPABASE_DB_HOST', default=''),
            'PORT': config('SUPABASE_DB_PORT', default='5432'),
        }
    }


# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]


# Internationalization
LANGUAGE_CODE = 'pt-br'
TIME_ZONE = 'America/Sao_Paulo'
USE_I18N = True
USE_TZ = True


# Static files (CSS, JavaScript, Images) — só o Django Admin usa isso de fato
# (o resto da interface carrega Bootstrap via CDN). WhiteNoise serve tudo
# direto do processo do Django, sem precisar de Nginx/CDN separado no Render.
STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STORAGES = {
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'


# Login/logout
LOGIN_URL = 'estoque:login'
LOGIN_REDIRECT_URL = 'estoque:recebimento'
LOGOUT_REDIRECT_URL = 'estoque:login'

# django.contrib.messages usa a tag "error" por padrão; o Bootstrap usa a
# classe "alert-danger" (não existe "alert-error") — este mapeamento faz
# {{ message.tags }} em base.html virar a classe Bootstrap certa.
MESSAGE_TAGS = {
    message_constants.ERROR: 'danger',
}
