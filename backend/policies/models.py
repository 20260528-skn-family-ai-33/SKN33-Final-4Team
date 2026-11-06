import uuid

from django.contrib.postgres.fields import ArrayField
from django.db import models
from pgvector.django import VectorField


def codes(name):
    return ArrayField(models.CharField(max_length=10), default=list, blank=True, verbose_name=name)


class Policy(models.Model):
    """온통청년 정책 한 건. raw = 담당자 이름만 뺀 원본, 나머지는 normalize.py 가 정리한 값"""

    policy_no = models.CharField("정책번호", max_length=30, primary_key=True)
    raw = models.JSONField("원본")
    content_hash = models.CharField("원본 해시", max_length=64)
    name = models.TextField("정책명")
    domains = ArrayField(models.CharField(max_length=10), default=list, blank=True, verbose_name="분야")
    apply_type = models.CharField("신청기간 구분", max_length=10)  # specific · always · closed · unknown
    periods = models.JSONField("신청기간", default=list)  # [{"start": "2026-11-01", "end": "2026-11-30"}]
    last_apply_end = models.DateField("마지막 신청 마감일", null=True, blank=True)  # 상시 · 모름 = NULL
    min_age = models.SmallIntegerField("최소 나이", null=True, blank=True)  # 둘 다 NULL = 나이 조건 모름
    max_age = models.SmallIntegerField("최대 나이", null=True, blank=True)
    zip_codes = codes("지역 코드")
    job_codes = codes("취업 요건")
    school_codes = codes("학력 요건")
    major_codes = codes("전공 요건")
    special_codes = codes("특화 요건")
    marriage_code = models.CharField("혼인 요건", max_length=10, blank=True)
    income_code = models.CharField("소득 조건 구분", max_length=10, blank=True)
    income_note = models.TextField("소득 조건 원문", blank=True)
    apply_url = models.TextField("신청 주소", blank=True)
    ref_urls = ArrayField(models.TextField(), default=list, blank=True, verbose_name="참고 주소")
    search_text = models.TextField("텍스트 검색 문장")
    embed_text = models.TextField("임베딩 문장")
    embedding = VectorField("임베딩", dimensions=1536, null=True, blank=True)
    embedded_hash = models.CharField("임베딩한 문장 · 모델 해시", max_length=64, blank=True)
    is_active = models.BooleanField("활성", default=True)
    updated_at = models.DateTimeField("수정 시각", auto_now=True)

    class Meta:
        db_table = "policy"
        verbose_name = verbose_name_plural = "청년 정책"


class PolicySync(models.Model):
    """정책 수집 이력. 마지막 성공 건의 data_date 가 데이터 기준일이다"""

    class Status(models.TextChoices):
        OK = "ok", "성공"
        FAILED = "failed", "실패"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    started_at = models.DateTimeField("시작 시각", auto_now_add=True)
    finished_at = models.DateTimeField("끝난 시각", null=True, blank=True)
    source = models.CharField("출처", max_length=10)  # file · api
    data_date = models.DateField("데이터 기준일")
    status = models.CharField("상태", max_length=10, choices=Status.choices)
    received = models.IntegerField("받은 수", default=0)
    changed = models.IntegerField("바뀐 수", default=0)
    deactivated = models.IntegerField("비활성화 수", default=0)
    unknown_regions = models.IntegerField("표에 없는 지역 코드 수", default=0)
    error = models.TextField("오류", blank=True)

    class Meta:
        db_table = "policy_sync"
        verbose_name = verbose_name_plural = "청년 정책 수집 이력"
