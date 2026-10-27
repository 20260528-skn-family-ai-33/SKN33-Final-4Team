"""
CODEF 카드 확인 명령 (저장하지 않고 출력만) — 카드 API 구현 전 데모 데이터 확인용

    python manage.py codef_card_check --connect          # 1) 현대카드를 connectedId 에 붙이기 (아이디·비밀번호 물어봄)
    python manage.py codef_card_check                    # 2) 보유 카드 목록 (현대카드는 --card-auth 붙이기)
    python manage.py codef_card_check --path <주소> --start 20260901 --end 20261027
                                                         # 3) 승인내역 · 청구내역 (주소·필수값은 CODEF 개발가이드 '카드 > 개인')
    python manage.py codef_card_check --path <주소> --extra memberStoreInfoType=1   # 필수값 더 넣기

--org 로 카드사 바꾸기 (기본 0302 현대카드 · 신한카드 0306).
카드 번호 · 카드 비밀번호를 매번 넣기 싫으면 루트 .env 에 CODEF_CARD_NO · CODEF_CARD_PASSWORD 를 적는다
(개발 확인용 · .env 는 깃에 안 올라감 · 실제 서비스 저장 방식과는 별개).
⚠️ 개수와 칸 이름만 보여 준다. --show 를 붙이면 첫 줄 값도 보이니(개인정보) 화면 공유 · 단톡방 주의.
"""
import json
import os
from getpass import getpass

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from easycodefpy import encrypt_rsa

from finance import codef

CARD_LIST = "/v1/kr/card/p/account/card-list"


def _card_secret(env_name, prompt):
    """.env 에 값이 있으면 그걸 쓰고(개발 확인용), 없으면 화면에 안 보이게 물어본다"""
    return (os.getenv(env_name) or "").strip() or getpass(prompt).strip()


def _clean(text):
    """앞뒤 공백 · 한/영 전환 때 섞이는 깨진 글자 빼기"""
    return "".join(ch for ch in (text or "").strip() if not 0xD800 <= ord(ch) <= 0xDFFF)


class Command(BaseCommand):
    help = "CODEF 카드 연결 · 조회 확인 (저장하지 않음)"

    def add_arguments(self, parser):
        parser.add_argument("--org", default="0302", help="카드사 코드 (현대 0302 · 신한 0306)")
        parser.add_argument("--connect", action="store_true", help="카드사 계정을 connectedId 에 붙이기")
        parser.add_argument("--id", dest="login_id", help="카드사 홈페이지 아이디 (안 주면 물어봄)")
        parser.add_argument("--path", default=CARD_LIST, help="조회할 CODEF 상품 주소 (기본: 보유 카드 목록)")
        parser.add_argument("--start", help="시작일 YYYYMMDD (승인 · 청구내역)")
        parser.add_argument("--end", help="종료일 YYYYMMDD")
        parser.add_argument("--extra", action="append", default=[], metavar="키=값", help="필수값 더 넣기 (여러 번 가능)")
        parser.add_argument("--show", action="store_true", help="첫 줄 값까지 보기 (개인정보 주의)")
        parser.add_argument("--card-auth", action="store_true",
                            help="조회 때도 카드 번호 · 카드 비밀번호를 같이 보내기 (현대카드처럼 요구하는 카드사)")

    def handle(self, *args, org, connect, login_id, path, start, end, extra, show, card_auth, **options):
        cid = settings.CODEF["CONNECTED_ID"]
        if not cid:
            raise CommandError(".env 에 CODEF_CONNECTED_ID 가 없어요. 은행부터 codef_connect 로 연결해 주세요.")
        if connect:
            return self._connect(cid, org, login_id)

        params = {"connectedId": cid, "organization": org, "inquiryType": "1"}
        if start:
            params["startDate"] = start
        if end:
            params["endDate"] = end
        if card_auth:   # 화면에 안 보이게 받고, 보낸 뒤 버린다
            params["cardNo"] = _clean(_card_secret("CODEF_CARD_NO", "카드 번호 16자리 (숫자만 · 화면에 안 보임): ")).replace("-", "")
            params["cardPassword"] = encrypt_rsa(_card_secret("CODEF_CARD_PASSWORD", "카드 비밀번호 (화면에 안 보임): "),
                                                 settings.CODEF["PUBLIC_KEY"])
        for kv in extra:
            k, _, v = kv.partition("=")
            params[k] = v
        try:
            data = codef._request(path, params)
        except codef.CodefError as e:
            raise CommandError(f"조회 실패 {e}")
        finally:
            params.pop("cardNo", None); params.pop("cardPassword", None)

        rows = data if isinstance(data, list) else [data]
        self.stdout.write(self.style.SUCCESS(f"{path} → {len(rows)}줄"))
        if rows and isinstance(rows[0], dict):
            for k, v in rows[0].items():
                if isinstance(v, list):
                    inner = list(v[0].keys()) if v and isinstance(v[0], dict) else []
                    self.stdout.write(f"  {k}: 목록 {len(v)}개  칸={inner}")
                else:
                    self.stdout.write(f"  {k}" + (f" = {v}" if show else ""))

    def _connect(self, cid, org, login_id=None):
        login_id = _clean(login_id or input(f"카드사({org}) 홈페이지 아이디: "))
        password = getpass("홈페이지 비밀번호 (화면에 안 보임): ")
        # 현대카드처럼 카드 번호를 같이 요구하는 카드사가 있다 (CF-12401 reqCardNo). 필요 없으면 Enter
        card_no = _clean(_card_secret("CODEF_CARD_NO", "카드 번호 16자리 (숫자만 · 화면에 안 보임 · 필요 없으면 Enter): ")).replace("-", "")
        card_pw = _card_secret("CODEF_CARD_PASSWORD", "카드 비밀번호 (화면에 안 보임 · 필요 없으면 Enter): ")
        key = settings.CODEF["PUBLIC_KEY"]
        account = {
            "countryCode": "KR", "businessType": "CD", "clientType": "P",   # CD = 카드
            "organization": org, "loginType": "1", "id": login_id,
            "password": encrypt_rsa(password, key),
        }
        if card_no:
            account["cardNo"] = card_no
        if card_pw:
            account["cardPassword"] = encrypt_rsa(card_pw, key)
        del password, card_no, card_pw   # 메모리에서도 바로 지우기
        raw = codef._client().add_account(codef._service_type(), {"connectedId": cid, "accountList": [account]})
        body = json.loads(raw)
        result = body.get("result", {})
        errors = (body.get("data") or {}).get("errorList") or []
        if result.get("code") != codef.SUCCESS or errors:
            # CF-04000 같은 겉 에러 안에 진짜 이유(errorList)가 들어 있다
            lines = [f"연결 실패 [{result.get('code')}] {result.get('message')} {result.get('extraMessage', '')}"]
            for e in errors:
                lines.append(f"  └ [{e.get('code')}] {e.get('message')} {e.get('extraMessage', '')}")
            raise CommandError("\n".join(lines))
        self.stdout.write(self.style.SUCCESS(f"카드사({org})를 기존 connectedId 에 붙였어요."))
