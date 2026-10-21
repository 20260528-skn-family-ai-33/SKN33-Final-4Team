# 프론트엔드(React) 가이드

화면은 **React**(`frontend/`)가 그리고, 데이터는 **Django API**(`backend/`)가 줍니다.
처음 설정(Python · Docker · `.env`)은 [`LOCAL_SETUP.md`](LOCAL_SETUP.md), API 규칙은 [`API_RULES.md`](API_RULES.md)를 봐 주세요.

```
브라우저 ── http://localhost:5173 ──▶ React (Vite 개발 서버)
                                      │  fetch('/api/home/')
                                      ▼  Vite 프록시
                                 Django (127.0.0.1:8000) ──▶ PostgreSQL
```

---

## 1. 처음 설정

### 1-1. Node.js 설치 (처음 한 번)

| | Windows | macOS |
|---|---|---|
| 설치 | nodejs.org → **LTS** 설치 파일 (npm 같이 설치됨) | `brew install node` |
| 확인 | `node -v` → **v22.22 이상** · `npm -v` | 같음 |

> 예전에 깔아둔 Node가 22.22보다 낮으면 nodejs.org에서 **LTS를 다시 설치**해요 (덮어써도 괜찮아요).

> 설치 후에는 **터미널을 새로 열어야** `node` 명령이 잡혀요.

### 1-2. 패키지 설치 (처음 한 번 + `package.json`이 바뀌었을 때)

```bash
cd frontend
npm install
```

`frontend/node_modules/` 폴더가 생기면 성공이에요. 이 폴더는 깃에 올라가지 않아요.

### 1-3. 실행 — 터미널 2개

| 터미널 | 할 일 | 명령 |
|---|---|---|
| 1 (가상환경 켜고) | Django API 서버 | `cd backend` → `python manage.py runserver` |
| 2 | React 화면 | `cd frontend` → `npm run dev` |

브라우저에서 **http://localhost:5173** 를 열면 돼요. 코드를 저장하면 화면이 바로 바뀌어요.
장고를 안 켜면 화면에 "데이터를 못 불러왔어요"가 나와요.

> 크롬 개발자 도구(F12)에서 휴대폰 모양 아이콘을 누르고 **iPhone 16**을 고르면 모바일 화면으로 볼 수 있어요. 확대 비율은 100%(`Ctrl/Cmd + 0`)로 맞춰 주세요.

---

## 2. 폴더 구조

```
frontend/
├── index.html            # 시작 페이지 (글꼴 · 제목)
├── vite.config.js        # /api 요청을 장고(8000)로 넘기는 프록시
├── package.json          # npm 패키지 목록 (react · react-router · lucide-react)
└── src/
    ├── main.jsx          # 앱 시작점 (CSS · 주소 장치 · 팝업 장치 연결)
    ├── App.jsx           # 주소 → 화면 연결
    ├── routes.js         # 화면 주소 목록 (PATHS)
    ├── pages/            # 화면 23개 (HomePage.jsx …)
    ├── popups/           # 팝업 (Popup 틀 · PopupProvider · 각 팝업)
    ├── components/       # 공통 부품 (Layout · Header · TabBar · Icon …)
    ├── utils/            # 도우미 (api.js · money.js · categories.js · usePicked.js · days.js …)
    └── styles/           # 디자인 CSS (app.css 원본 · shell.css 보정)
```

| 부품 | 하는 일 |
|---|---|
| `Layout` | 모든 화면의 틀. 상단 바, 본문, 아래 버튼, 팝업 자리, 하단 탭 |
| `Icon` | `<Icon name="CreditCard" />`처럼 이름으로 아이콘 그리기 |
| `StepBar` | 첫 설정 흐름의 "7단계 중 N단계" 막대 |
| `AcctPick` · `InstRow` | 계좌 고르기 카드 · 기관 한 줄 |
| `LockIcon` | 카테고리 자물쇠 아이콘 |

| 도우미 (`utils/`) | 하는 일 |
|---|---|
| `api.js` | `useApi`(읽기) · `apiSend`(저장) — 5번 참고 |
| `money.js` | `won(420000)` → `420,000원` |
| `categories.js` | **소비 카테고리 목록 · 아이콘. 카테고리가 필요하면 꼭 여기서만 가져다 쓴다** (`SPEND_CATS` 10개 · `ETC` 기타 · `PENDING` 확인 필요 · `catIcon(이름)`). 화면에 카테고리 이름을 직접 적지 않는다. 기준이 바뀌면 이 파일만 고친다 |
| `usePicked.js` | 여러 개 고르기 (6번 참고) |
| `days.js` | 날짜 목록 · 급여 주기 계산 |
| `mockRouter.js` · `mockConnect.js` | **임시**. 챗봇 라우터 · 계좌 연결 API가 생기기 전까지 화면 흐름만 보여 주는 가짜 |

---

## 3. 화면 하나 추가하기 (3단계)

**① `src/routes.js`에 주소 추가**
```js
export const PATHS = {
  // ...
  report: '/report',
}
```

**② `src/pages/ReportPage.jsx` 만들기**
```jsx
import Layout from '../components/Layout.jsx'

export default function ReportPage() {
  return (
    <Layout title="소비 분석" showBack>
      <h1>이번 달 소비</h1>
      <div className="mc-card">내용</div>
    </Layout>
  )
}
```

**③ `src/App.jsx`의 `ROUTES`에 한 줄 추가**
```jsx
import ReportPage from './pages/ReportPage.jsx'
// ...
[PATHS.report, ReportPage],
```

### `Layout`에 넣을 수 있는 값

| 값 | 뜻 | 예 |
|---|---|---|
| `title` | 상단 제목 | `"카테고리 예산"` |
| `showBack` | 뒤로 가기 버튼 | `showBack` |
| `activeTab` | 탭 화면이면 켜진 탭. 넣으면 하단 탭과 마이 아이콘이 생겨요 | `"home"` · `"txs"` · `"budget"` · `"fund"` · `"assistant"` |
| `foot` | 화면 맨 아래 고정 버튼 | `foot={<button className="mc-primary">다음</button>}` |

### 다른 화면으로 가기
```jsx
import { useNavigate } from 'react-router'
import { PATHS } from '../routes.js'

const navigate = useNavigate()
<button onClick={() => navigate(PATHS.budget)}>예산 보기</button>
```

---

## 4. 팝업

```jsx
import { usePopup } from '../popups/context.js'
import CalcPopup from '../popups/CalcPopup.jsx'

const { openPopup } = usePopup()
<button onClick={() => openPopup(<CalcPopup />)}>계산 근거</button>
```

새 팝업은 `Popup` 틀 안에 내용만 넣으면 돼요. 배경 누르기, `닫기 ×`, Esc로 닫는 건 틀이 알아서 해요.
```jsx
import Popup from './Popup.jsx'

export default function HelloPopup({ name }) {        // 값은 props 로 받기
  return (
    <Popup>                                            {/* 큰 시트는 <Popup sub> */}
      <h2>안녕 {name}</h2>
    </Popup>
  )
}
```
닫기는 `const { closePopup } = usePopup()`로 해요. 팝업 안에서 다른 팝업을 열면 갈아끼워져요. 다른 화면으로 가면 팝업은 저절로 사라져요.

---

## 5. 데이터 (장고 API)

```jsx
import { useApi, apiSend } from '../utils/api.js'
import ApiStatus from '../components/ApiStatus.jsx'

// 읽기
const { data, error } = useApi('/api/home/')
if (!data) return <Layout title="홈"><ApiStatus error={error} /></Layout>   // 불러오는 중 · 실패

// 저장 (POST · PATCH · DELETE) — 보안 토큰(CSRF)은 자동으로 붙어요
try {
  await apiSend('/api/budgets/fixed/', 'POST', { name: '월세', amount: 700000 })
} catch (err) {
  alert(err.message)     // err.fields = 칸별 에러
}
```

- 키 이름은 **camelCase**(`livingUsed`)예요. 장고가 알아서 바꿔 줘요.
- 돈은 **원 단위 정수**예요. 화면에 보여 줄 때는 `won(420000)` → `420,000원`.
- 아직 API가 없는 화면은 파일 위쪽에 예시 숫자를 적어 뒀어요 (`// 시안 예시값` 주석). API가 생기면 `useApi`로 바꿔요.

---

## 6. 상태 (눌러서 바뀌는 것)

| 쓰임 | 코드 |
|---|---|
| 하나만 고르기 | `const [dir, setDir] = useState(null)` → `aria-pressed={dir === name}` · `onClick={() => setDir(name)}` |
| 여러 개 고르기 | `const banks = usePicked(['신한'])` → `banks.has(x)` · `banks.toggle(x)` · `banks.list` |
| 입력칸 | `const [pw, setPw] = useState('')` → `value={pw} onChange={(e) => setPw(e.target.value)}` |

> 버튼이 "눌린 모양"인지는 디자인 CSS가 `aria-pressed="true"`를 보고 정해요.

---

## 7. 디자인 · JSX 규칙

- 디자인은 `styles/app.css`(프로토타입 원본)에 다 있어요. **원본은 되도록 고치지 말고**, 고칠 게 있으면 `shell.css`에 덧붙여요.
- 클래스 이름은 `mc-`로 시작해요 (`mc-card`, `mc-primary`, `mc-row`, `mc-muted` …). 비슷한 기존 화면을 열어서 따라 쓰면 편해요.

| HTML | JSX (React) |
|---|---|
| `class="mc-card"` | `className="mc-card"` |
| `for="birth"` | `htmlFor="birth"` |
| `style="width:40%"` | `style={{ width: '40%' }}` |
| `<br>` · `<input>` | `<br />` · `<input />` (꼭 닫기) |
| `{% if %}` | `{조건 && <div>…</div>}` |
| `{% for %}` | `{목록.map((x) => <div key={x.id}>…</div>)}` (`key` 필수) |
| `maxlength` · `inputmode` | `maxLength` · `inputMode` |

### 아이콘
- `<Icon name="Wallet" />` 이름은 [lucide.dev/icons](https://lucide.dev/icons)에서 찾아요 (`credit-card` → `CreditCard`).
- 새 아이콘은 `components/Icon.jsx` 위쪽 **두 군데(import · ICONS)**에 이름을 추가해야 나와요. 안 넣으면 동그라미가 그려져요.

---

## 8. 커밋 전에

```bash
npm run lint     # 경고 0개
npm run build    # 에러 없이 끝나기
```

| 올린다 | 안 올린다 |
|---|---|
| `src/` · `package.json` · `package-lock.json` | `node_modules/` · `dist/` (`.gitignore`에 있음) |

> `npm install 패키지`로 새 패키지를 넣었으면 `package.json`과 `package-lock.json`을 **같이** 커밋하고, 팀에 "npm install 다시 해 주세요"라고 알려 주세요.
> `npm install`은 **꼭 `frontend` 폴더 안에서** 실행해요. 밖에서 하면 엉뚱한 곳에 `package.json`이 생겨요.

---

## 9. 자주 나는 에러

| 에러 | 원인 | 해결 |
|---|---|---|
| `npm: command not found` / `'npm'은(는) … 아닙니다` | Node.js 미설치 · 터미널을 안 새로 염 | 1-1 설치 후 터미널 새로 열기 |
| (Windows) `npm.ps1 파일을 로드할 수 없습니다` | PowerShell 실행 정책 | `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` 한 번 실행 |
| `Failed to resolve import "react-router"` | `npm install` 안 함 | `frontend`에서 `npm install` |
| 화면에 "데이터를 못 불러왔어요" | 장고 서버 꺼짐 | 터미널 1에서 `python manage.py runserver` |
| `Port 5173 is in use` | 개발 서버가 이미 켜져 있음 | 켜진 창을 쓰거나 `Ctrl+C`로 끄고 다시 |
| 아이콘이 동그라미로 나옴 | `Icon.jsx` 목록에 없는 이름 | 7. 아이콘 참고 |
| 저장 시 `CSRF Failed` | `fetch`를 직접 씀 | `apiSend` 사용 |
| 화면이 하얗게 비어 있음 | 코드 에러 | F12 → Console의 빨간 글씨 확인 |
