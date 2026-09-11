import django
from django.conf import settings


def pytest_configure():
    if not settings.configured:
        django.setup()
