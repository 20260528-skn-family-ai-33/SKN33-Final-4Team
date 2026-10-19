// 내 정보 입력
// 생년월일을 넣으면 만 나이를, 우선순위를 누르면 순위 번호를 바로 보여 준다
import { useState } from 'react'
import { useNavigate } from 'react-router'
import Layout from '../components/Layout.jsx'
import StepBar from '../components/StepBar.jsx'
import { usePicked } from '../utils/usePicked.js'
import { PATHS } from '../routes.js'

const CATS = ['식비', '주거/통신', '교통/차량', '생활용품', '건강/미용', '여가/관계']
const GENDERS = ['남성', '여성']
const CARS = [['yes', '차가 있어요', '자차 보유'], ['no', '차가 없어요', '대중교통 · 기타']]

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
  return n < CATS.length ? '필수 3순위 완료 · 4위부터는 선택이에요' : '모든 카테고리 순위를 정했어요'
}

export default function ProfilePage() {
  const navigate = useNavigate()
  const [birth, setBirth] = useState('')
  const [gender, setGender] = useState(null)
  const [car, setCar] = useState(null)
  const prio = usePicked()  // 누른 순서 = 우선순위

  const a = ageInfo(birth)
  const onSubmit = (e) => {
    e.preventDefault()
    navigate(PATHS.connect)  // 저장은 API 연결 후
  }

  return (
    <Layout
      title="내 정보 입력"
      showBack
      foot={<button className="mc-primary" form="mc-form" type="submit">저장하고 계속</button>}
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
              {GENDERS.map((g) => <button key={g} type="button" aria-pressed={gender === g} onClick={() => setGender(g)}>{g}</button>)}
            </div>
            <p className="mc-mini-note">본인 성별</p>
          </div>
        </div>

        <div className="mc-card">
          <h2>자차 보유</h2>
          <div className="mc-seg">
            {CARS.map(([k, label, sub]) => (
              <button key={k} type="button" aria-pressed={car === k} onClick={() => setCar(k)}><strong>{label}</strong><small>{sub}</small></button>
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
        <div className="mc-error" role="alert"></div>
      </form>
    </Layout>
  )
}
