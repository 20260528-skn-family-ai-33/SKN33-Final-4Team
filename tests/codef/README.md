# CODEF 연결 확인 스크립트

CODEF 데모 API가 잘 연결되는지 직접 돌려보는 스크립트예요. (Django 자동 테스트 아님)

**프로젝트 맨 위 폴더에서** 실행해요. 결과 파일도 그 위치 기준으로 저장돼요.

| 파일 | 하는 일 | 실행 |
|---|---|---|
| `test_codef.py` | 토큰 발급·연결 확인 | `python tests/codef/test_codef.py` |
| `test_transactions.py` | 계좌 거래내역 조회 → `transactions_raw.json` | `python tests/codef/test_transactions.py` |
| `test_card_transactions.py` | 카드 승인내역 조회 → `data/raw/` | `python tests/codef/test_card_transactions.py` |

`.env`는 위쪽 폴더에서 자동으로 찾아요. 결과 파일에는 거래 정보가 들어 있으니 깃에 올리지 않아요.

`test_codef.py`, `test_transactions.py`는 `.gitignore`에 들어 있어서 깃에는 안 올라가요. 각자 컴퓨터에만 있어요.
