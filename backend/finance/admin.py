"""관리자 화면(/admin)에 이 앱의 모든 테이블 등록 (확인·테스트용 기본 화면)"""
from django.apps import apps
from django.contrib import admin
from django.db import models

SKIP = (models.JSONField, models.TextField)


def make_admin(model):
    cols = [f.name for f in model._meta.concrete_fields if not isinstance(f, SKIP)][:7]
    return type(f"{model.__name__}Admin", (admin.ModelAdmin,), {"list_display": cols, "list_per_page": 50})


for _model in apps.get_app_config(__name__.split(".")[0]).get_models():
    admin.site.register(_model, make_admin(_model))
