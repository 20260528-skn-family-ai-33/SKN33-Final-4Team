// 내 정보 입력
// 생년월일을 넣으면 만 나이를, 우선순위를 누르면 순위 번호를 바로 보여 준다
// 카테고리 최소 금액(선택)은 팝업에서 받는다 — 우선순위와 상관없이 어느 카테고리든 (2026-10-21)
// 자취 여부(필수)로 지킬 돈 비율이 정해진다 — 고르면 비율을 바로 보여 준다 (2026-10-27, utils/direction.js)
// GET · PUT /api/profile/ (docs/api/profile.md). 서버 값이 기준 — 저장에 성공해야 다음 화면으로 간다 (11/03 S1)
import { useState } from 'react'
import { useNavigate } from 'react-router'
import Layout from '../components/Layout.jsx'
import StepBar from '../components/StepBar.jsx'
import ApiStatus from '../components/ApiStatus.jsx'
import { usePicked } from '../utils/usePicked.js'
import { PRIORITY_CATS as CATS, MIN_CATS, SPEND_CATS, ETC, catName } from '../utils/categories.js'
import { usePopup } from '../popups/context.js'
import CatMinPopup from '../popups/CatMinPopup.jsx'
import { manwon } from '../utils/adjust.js'
import { apiSend, useApi } from '../utils/api.js'
import { HOUSING, HOUSING_KEYS, rateLine } from '../utils/direction.js'
import { PATHS } from '../routes.js'

// 화면 이름 ↔ API 코드 (docs/api/profile.md '보내는 값')
const GENDERS = [['male', '남성'], ['female', '여성']]
const CARS = [[true, '차가 있어요', '자차 보유'], [false, '차가 없어요', '대중교통 · 기타']]
const CODE_BY_NAME = Object.fromEntries([...SPEND_CATS, ETC].map((c) => [c.name, c.code]))
const birthDigits = (iso) => (iso ? iso.slice(2).replaceAll('-', '') : '')  // '1999-03-15' → '990315'

// 990315 → { age: 만 나이, band: '20대 후반', y, mm, dd } / 잘못된 날짜면 { bad: true } / 6자리 전이면 null
function ageInfo(raw) {
  if (raw.length < 6) return null
  const now = new Date()
  const yy = +raw.slice(0, 2), mm = +raw.slice(2, 4), dd = +raw.slice(4, 6)
  const y = (yy > now.getFullYear() % 100 ? 1900 : 2000) + yy
  const d = new Date(y, mm - 1, dd)
  if (mm < 1 || mm > 12 || d.getMonth() !== mm - 1 || d.getDate() !== dd) return { bad: true }
  let age = now.getFullYear() - y
  if (now.getMonth() + 1 < mm || (now.getMonth() + 1 === mm && now.getDate() < dd)) age--
  if (age < 0 || age > 120) return { bad: true }
  const r = age % 10
  const band = age < 20 ? '10대' : `${Math.floor(age / 10) * 10}대 ${r < 4 ? '초반' : r < 7 ? '중반' : '후반'}`
  return { age, band, y, mm, dd }
}

function prioStatus(n) {
  if (n < 3) return <><b>{3 - n}개</b> 더 골라 주세요 · 1~3위는 필수예요</>
  return n < CATS.length ? '필수 3순위 완료 · 4위부터는 원하는 만큼 골라 주세요' : '모든 카테고리 순위를 정했어요'
}

export default function ProfilePage() {
  const { data, error } = useApi('/api/profile/')
  if (!data) return <Layout title="내 정보 입력" showBack><ApiStatus error={error} /></Layout>
  return <ProfileForm saved={data} />
}

// 서버 값으로 처음 채운 뒤 고친다
function ProfileForm({ saved }) {
  const navigate = useNavigate()
  const [birth, setBirth] = useState(() => birthDigits(saved.birthDate))
  const [gender, setGender] = useState(saved.gender)  // 'male' | 'female' | null
  const [car, setCar] = useState(saved.hasCar)  // true | false | null
  const [housing, setHousing] = useState(saved.housingType)  // 'alone' | 'family' | null
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  // 누른 순서 = 우선순위 (응답은 순위순). 예산에서 뺀 카테고리의 예전 순위는 빼고, 저장할 때 남은 순서대로 1위부터 다시 매긴다
  const prio = usePicked(saved.priorities.map((p) => catName(p.category)).filter((c) => CATS.includes(c)))
  const { openPopup } = usePopup()
  // 카테고리 최소 금액 { 식비: 300000 } — 설정한 것만
  const [mins, setMins] = useState(() => Object.fromEntries(saved.categoryMinimums.map((m) => [catName(m.category), m.minAmount])))
  const minList = MIN_CATS.filter((c) => mins[c])  // 카테고리 순서대로. 예산에서 뺀 카테고리의 예전 값은 다시 보내지 않는다

  const a = ageInfo(birth)
  const onSubmit = async (e) => {
    e.preventDefault()
    if (busy) return
    const missing = !a || a.bad ? '생년월일 6자리를 확인해 주세요.'
      : !gender ? '성별을 골라 주세요.'
      : !housing ? '자취 여부를 골라 주세요. 지킬 돈을 나누는 기준이에요.'
      : car === null ? '자차 보유 여부를 골라 주세요.'
      : prio.list.length < 3 ? '소비 우선순위를 3개 이상 골라 주세요.' : ''
    if (missing) { setError(missing); return }
    setBusy(true)
    setError('')
    try {
      await apiSend('/api/profile/', 'PUT', {
        birthDate: `${a.y}-${String(a.mm).padStart(2, '0')}-${String(a.dd).padStart(2, '0')}`,
        gender,
        housingType: housing,
        hasCar: car,
        priorities: prio.list.map((name, i) => ({ category: CODE_BY_NAME[name], rank: i + 1 })),
        categoryMinimums: minList.map((name) => ({ category: CODE_BY_NAME[name], minAmount: mins[name] })),
      })
      navigate(PATHS.connect)
    } catch (err) {
      // 칸별 오류가 있으면 첫 문장을 보여 준다. 입력값은 그대로 둔다
      const first = Object.values(err.fields ?? {}).flat().find((m) => typeof m === 'string')
      setError(first ?? err.message)
      setBusy(false)
    }
  }

  return (
    <Layout
      title="내 정보 입력"
      showBack
      foot={<button className="mc-primary" form="mc-form" type="submit" disabled={busy}>{busy ? '저장 중…' : '저장하고 계속'}</button>}
    >
      <StepBar step={1} />
      <div className="mc-eyebrow">내 정보 입력</div>
      <h1>나에게 맞는 예산을<br />만들기 위해 알려주세요</h1>
      <p className="mc-muted">비슷한 연령대의 소비 통계와 비교해 예산을 추천해요.</p>
      <form className="mc-form" id="mc-form" onSubmit={onSubmit}>
        <div className="mc-card mc-two">
          <div>
            <h2 className="mc-mini-h">생년월일</h2>
            <label className="mc-birth-row" htmlFor="mc-birth">
              <input aria-describedby="mc-age" autoComplete="bday" id="mc-birth" inputMode="numeric" maxLength={6} name="birth" placeholder="990315" type="text"
                value={birth} onChange={(e) => setBirth(e.target.value.replace(/\D/g, '').slice(0, 6))} />
              <span aria-live="polite" id="mc-age">
                {a && (a.bad
                  ? <span className="mc-age-badge is-bad">날짜 확인</span>
                  : <span className="mc-age-badge">만 {a.age}세</span>)}
              </span>
            </label>
            <p className="mc-mini-note" id="mc-birth-note">
              {a && !a.bad ? `${a.band} · ${a.y}.${String(a.mm).padStart(2, '0')}.${String(a.dd).padStart(2, '0')}생` : '주민번호 앞 6자리'}
            </p>
          </div>
          <div>
            <h2 className="mc-mini-h">성별</h2>
            <div className="mc-seg mc-seg-sm">
              {GENDERS.map(([k, label]) => <button key={k} type="button" aria-pressed={gender === k} onClick={() => setGender(k)}>{label}</button>)}
            </div>
            <p className="mc-mini-note">본인 성별</p>
          </div>
        </div>

        <div className="mc-card">
          <h2>자취 여부</h2>
          <div className="mc-seg">
            {HOUSING_KEYS.map((k) => (
              <button key={k} type="button" aria-pressed={housing === k} onClick={() => { setHousing(k); setError('') }}>
                <strong>{HOUSING[k].label}</strong><small>{HOUSING[k].sub}</small>
              </button>
            ))}
          </div>
          <p className="mc-picked" aria-live="polite" style={{ marginTop: 10 }}>
            {housing ? <>{HOUSING[housing].name} 기준 · <b>{rateLine(housing)}</b></> : '자취 여부에 따라 생활비와 저축 비율이 달라져요.'}
          </p>
        </div>

        <div className="mc-card">
          <h2>자차 보유</h2>
          <div className="mc-seg">
            {CARS.map(([k, label, sub]) => (
              <button key={label} type="button" aria-pressed={car === k} onClick={() => setCar(k)}><strong>{label}</strong><small>{sub}</small></button>
            ))}
          </div>
        </div>

        <div className="mc-card">
          <div className="mc-row">
            <h2>소비 우선순위</h2>
            {prio.list.length > 0 && <button type="button" className="mc-link" onClick={() => prio.set([])}>다시 고르기</button>}
          </div>
          <p className="mc-picked" style={{ margin: '2px 0 12px' }}>중요한 카테고리부터 차례로 눌러 주세요. 다시 누르면 빠져요.</p>
          <div className="mc-prio-grid">
            {CATS.map((c) => {
              const rank = prio.list.indexOf(c)  // -1 이면 안 고름
              const cls = ['mc-prio', rank >= 0 && 'on', rank >= 0 && rank < 3 && 'must'].filter(Boolean).join(' ')
              return (
                <button key={c} type="button" className={cls} aria-pressed={rank >= 0} onClick={() => prio.toggle(c)}>
                  <span className="mc-prio-rank">{rank >= 0 ? rank + 1 : ''}</span><span>{c}</span>
                </button>
              )
            })}
          </div>
          <p className="mc-picked" id="mc-prio-status" style={{ marginTop: 12 }}>{prioStatus(prio.list.length)}</p>
        </div>

        <div className="mc-card">
          <div className="mc-row">
            <h2>카테고리 최소 금액 <span className="mc-opt">선택</span></h2>
            <button type="button" className="mc-link" onClick={() => openPopup(<CatMinPopup value={mins} onSave={setMins} />)}>
              {minList.length ? '수정' : '설정하기'} ›
            </button>
          </div>
          {minList.length ? (
            <div className="mc-min-chips">
              {minList.map((c) => <span key={c} className="mc-min-chip">{c} <b>{manwon(mins[c])}</b></span>)}
            </div>
          ) : (
            <p className="mc-picked" style={{ marginTop: 6 }}>이만큼은 꼭 남겨 둘 카테고리가 있다면 설정해 주세요. 그 밑으로는 예산을 줄이지 않아요.</p>
          )}
        </div>
        <div className="mc-error" role="alert">{error}</div>
      </form>
    </Layout>
  )
}
