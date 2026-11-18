// 내 정보 입력
// 생년월일을 넣으면 만 나이를, 우선순위를 누르면 순위 번호를 바로 보여 준다
// 카테고리 최소 금액(선택)은 팝업에서 받는다 — 우선순위와 상관없이 어느 카테고리든 (2026-10-21)
// 자취 여부(필수)로 지킬 돈 비율이 정해진다 — 고르면 비율을 바로 보여 준다 (2026-10-27, utils/direction.js)
// GET · PUT /api/profile/ (docs/api/profile.md). 서버 값이 기준 — 저장에 성공해야 다음 화면으로 간다 (11/03 S1)
// 마이에서 들어오면(location.state.from === 'my') 단계 표시줄 없이 한 화면에 모든 칸, 저장 뒤 마이로 돌아간다
// 사는 지역(필수, 2026-11-11 멘토 요청): 시도 → 시군구 드롭다운. 목록은 GET /api/policies/regions/, 청년 정책 찾기의 기본 지역
// 11/18 시안 O3 라임 스토리 카드: 가입 흐름에서는 한 화면에 질문 하나를 큰 글씨로. [다음] = 지금 질문 확인 → 다음 질문
//   이미 저장된 값이 있으면(뒤로 와서 다시 들어옴) 마지막 확인 목록부터 보여 주고, 줄을 누르면 그 질문으로 돌아간다
import { useState } from 'react'
import { useLocation, useNavigate } from 'react-router'
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
const pad2 = (n) => String(n).padStart(2, '0')

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
  const manage = useLocation().state?.from === 'my'
  const [birth, setBirth] = useState(() => birthDigits(saved.birthDate))
  const [gender, setGender] = useState(saved.gender)  // 'male' | 'female' | null
  const [car, setCar] = useState(saved.hasCar)  // true | false | null
  const [housing, setHousing] = useState(saved.housingType)  // 'alone' | 'family' | null
  const regions = useApi('/api/policies/regions/').data?.items ?? []  // [{ code, name, regions: [{ code, name }] }]
  const [region, setRegion] = useState(saved.regionCode ?? '')  // 시군구 코드
  const [pickedSido, setSido] = useState('')
  const sido = pickedSido || (regions.find((t) => t.regions.some((r) => r.code === region))?.code ?? '')
  const areas = regions.find((t) => t.code === sido)?.regions ?? []
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  // 누른 순서 = 우선순위 (응답은 순위순). 예산에서 뺀 카테고리의 예전 순위는 빼고, 저장할 때 남은 순서대로 1위부터 다시 매긴다
  const prio = usePicked(saved.priorities.map((p) => catName(p.category)).filter((c) => CATS.includes(c)))
  const { openPopup } = usePopup()
  // 카테고리 최소 금액 { 식비: 300000 } — 설정한 것만
  const [mins, setMins] = useState(() => Object.fromEntries(saved.categoryMinimums.map((m) => [catName(m.category), m.minAmount])))
  const minList = MIN_CATS.filter((c) => mins[c])  // 카테고리 순서대로. 예산에서 뺀 카테고리의 예전 값은 다시 보내지 않는다
  const a = ageInfo(birth)

  const sidoName = regions.find((t) => t.code === sido)?.name
  const areaName = areas.find((r) => r.code === region)?.name
  // 질문 하나 = label(마이에서 고칠 때 제목) · ask(쿠숑이 질문) · check(빠졌을 때 안내, 없으면 '') · answer(대답 말풍선) · body(입력 칸)
  const steps = [
    {
      key: 'birth', label: '생년월일', ask: '생일이 언제예요?',
      check: () => (!a || a.bad ? '생년월일 6자리를 확인해 주세요.' : ''),
      answer: a && !a.bad ? `${a.y}.${pad2(a.mm)}.${pad2(a.dd)} · 만 ${a.age}세` : '',
      body: <>
        <label className="mc-birth-row" htmlFor="mc-birth">
          <input aria-describedby="mc-age" aria-label="생년월일 6자리" autoComplete="bday" id="mc-birth" inputMode="numeric" maxLength={6} name="birth" placeholder="990315" type="text"
            value={birth} onChange={(e) => { setBirth(e.target.value.replace(/\D/g, '').slice(0, 6)); setError('') }} />
          <span aria-live="polite" id="mc-age">
            {a && (a.bad
              ? <span className="mc-age-badge is-bad">날짜 확인</span>
              : <span className="mc-age-badge">만 {a.age}세</span>)}
          </span>
        </label>
        <p className="mc-mini-note" id="mc-birth-note">
          {a && !a.bad ? `${a.band} · ${a.y}.${pad2(a.mm)}.${pad2(a.dd)}생` : '주민번호 앞 6자리'}
        </p>
      </>,
    },
    {
      key: 'gender', label: '성별', ask: '성별을 알려 주세요',
      check: () => (!gender ? '성별을 골라 주세요.' : ''),
      answer: GENDERS.find(([k]) => k === gender)?.[1] ?? '',
      body: <>
        <div className="mc-seg mc-seg-sm">
          {GENDERS.map(([k, label]) => <button key={k} type="button" aria-pressed={gender === k} onClick={() => { setGender(k); setError('') }}>{label}</button>)}
        </div>
        {manage && <p className="mc-mini-note">본인 성별</p>}
      </>,
    },
    {
      key: 'region', label: '사는 지역', ask: '어느 지역에 살고 있나요?',
      check: () => (!region ? '사는 지역을 골라 주세요.' : ''),
      answer: sidoName && areaName ? (sidoName === areaName ? areaName : `${sidoName} ${areaName}`) : '',
      body: <>
        <div className="mc-pair" style={{ marginBottom: 0 }}>
          <label className="mc-field">시도
            <select aria-label="시도" value={sido} onChange={(e) => { setSido(e.target.value); setRegion(''); setError('') }}>
              <option value="">고르기</option>
              {regions.map((t) => <option key={t.code} value={t.code}>{t.name}</option>)}
            </select>
          </label>
          <label className="mc-field">시군구
            <select aria-label="시군구" value={region} disabled={!sido} onChange={(e) => { setRegion(e.target.value); setError('') }}>
              <option value="">고르기</option>
              {areas.map((r) => <option key={r.code} value={r.code}>{r.name}</option>)}
            </select>
          </label>
        </div>
        <p className="mc-picked" style={{ marginTop: 10 }}>청년 정책을 찾을 때 이 지역을 기본으로 써요.</p>
      </>,
    },
    {
      key: 'housing', label: '자취 여부', ask: '지금은 혼자 살고 있나요?',
      check: () => (!housing ? '자취 여부를 골라 주세요. 지킬 돈을 나누는 기준이에요.' : ''),
      answer: housing ? HOUSING[housing].label : '',
      body: <>
        <div className="mc-seg">
          {HOUSING_KEYS.map((k) => (
            <button key={k} type="button" aria-pressed={housing === k} onClick={() => { setHousing(k); setError('') }}>
              <strong>{HOUSING[k].label}</strong><small>{HOUSING[k].sub}</small>
            </button>
          ))}
        </div>
        <p className="mc-picked" aria-live="polite" style={{ marginTop: 10 }}>
          {housing ? <>{HOUSING[housing].short} 기준 · <b>{rateLine(housing)}</b></> : '자취 여부에 따라 생활비와 저축 비율이 달라져요.'}
        </p>
      </>,
    },
    {
      key: 'car', label: '자차 보유', ask: '차가 있나요?',
      check: () => (car === null ? '자차 보유 여부를 골라 주세요.' : ''),
      answer: CARS.find(([k]) => k === car)?.[1] ?? '',
      body: (
        <div className="mc-seg">
          {CARS.map(([k, label, sub]) => (
            <button key={label} type="button" aria-pressed={car === k} onClick={() => { setCar(k); setError('') }}><strong>{label}</strong><small>{sub}</small></button>
          ))}
        </div>
      ),
    },
    {
      key: 'prio', label: '소비 우선순위', ask: '어디에 쓰는 돈이 가장 중요해요?',
      check: () => (prio.list.length < 3 ? '소비 우선순위를 3개 이상 골라 주세요.' : ''),
      answer: prio.list.length ? prio.list.slice(0, 3).join(' · ') + (prio.list.length > 3 ? ` 외 ${prio.list.length - 3}개` : '') : '',
      reset: prio.list.length > 0 && <button type="button" className="mc-link" onClick={() => prio.set([])}>다시 고르기</button>,
      body: <>
        <p className="mc-picked" style={{ margin: '2px 0 12px' }}>중요한 카테고리부터 차례로 눌러 주세요. 다시 누르면 빠져요.</p>
        <div className="mc-prio-grid">
          {CATS.map((c) => {
            const rank = prio.list.indexOf(c)  // -1 이면 안 고름
            const cls = ['mc-prio', rank >= 0 && 'on', rank >= 0 && rank < 3 && 'must'].filter(Boolean).join(' ')
            return (
              <button key={c} type="button" className={cls} aria-pressed={rank >= 0} onClick={() => { prio.toggle(c); setError('') }}>
                <span className="mc-prio-rank">{rank >= 0 ? rank + 1 : ''}</span><span>{c}</span>
              </button>
            )
          })}
        </div>
        <p className="mc-picked" id="mc-prio-status" style={{ marginTop: 12 }}>{prioStatus(prio.list.length)}</p>
      </>,
    },
    {
      key: 'mins', label: '카테고리 최소 금액', ask: '꼭 남겨 둘 금액이 있나요?', optional: true,
      check: () => '',
      answer: minList.length ? minList.map((c) => `${c} ${manwon(mins[c])}`).join(' · ') : '없어요',
      reset: <button type="button" className="mc-link" onClick={() => openPopup(<CatMinPopup value={mins} onSave={setMins} />)}>{minList.length ? '수정' : '설정하기'} ›</button>,
      body: minList.length ? (
        <div className="mc-min-chips">
          {minList.map((c) => <span key={c} className="mc-min-chip">{c} <b>{manwon(mins[c])}</b></span>)}
        </div>
      ) : (
        <p className="mc-picked" style={{ marginTop: 6 }}>이만큼은 꼭 남겨 둘 카테고리가 있다면 설정해 주세요. 그 밑으로는 예산을 줄이지 않아요.</p>
      ),
    },
  ]
  const last = steps.length

  // 처음 가입이면 첫 질문부터 차례로. 저장된 값이 있으면 빠진 질문만 묻고, 없으면 모두 대답한 상태
  const fresh = !saved.birthDate
  const [reached, setReached] = useState(fresh ? 0 : last - 1)  // 한 번이라도 본 질문 중 가장 뒤
  const [at, setAt] = useState(() => {
    if (fresh) return 0
    const i = steps.findIndex((s) => s.check())
    return i < 0 ? last : i
  })
  // [다음]을 누르면 갈 곳: 아직 안 본 질문이나 빠진 질문 중 가장 앞 (-1 = 다 끝남 → 저장)
  const nextAt = steps.findIndex((s, i) => i > at && (i > reached || s.check()))

  const save = async () => {
    const missing = steps.map((s) => s.check()).find(Boolean)
    if (missing) { setError(missing); return }
    setBusy(true)
    setError('')
    try {
      const updated = await apiSend('/api/profile/', 'PUT', {
        birthDate: `${a.y}-${pad2(a.mm)}-${pad2(a.dd)}`,
        gender,
        housingType: housing,
        hasCar: car,
        regionCode: region,
        priorities: prio.list.map((name, i) => ({ category: CODE_BY_NAME[name], rank: i + 1 })),
        categoryMinimums: minList.map((name) => ({ category: CODE_BY_NAME[name], minAmount: mins[name] })),
      })
      if (manage) navigate(PATHS.my, { replace: true, state: { budgetNote: true } })
      else navigate(updated?.onboardingNextStep === 'salary' ? PATHS.salary : PATHS.connect)
    } catch (err) {
      // 칸별 오류가 있으면 첫 문장을 보여 준다. 입력값은 그대로 둔다
      const first = Object.values(err.fields ?? {}).flat().find((m) => typeof m === 'string')
      setError(first ?? err.message)
      setBusy(false)
    }
  }

  const onSubmit = (e) => {
    e.preventDefault()
    if (busy) return
    if (manage || at >= last) return save()
    const missing = steps[at].check()
    if (missing) { setError(missing); return }
    setError('')
    if (nextAt < 0) { setAt(last); return save() }
    setReached(Math.max(reached, nextAt))
    setAt(nextAt)
  }
  const edit = (i) => { setError(''); setAt(i) }

  const doneAll = at >= last || nextAt < 0
  const label = busy ? '저장 중…' : manage ? '저장' : doneAll ? '저장하고 계속' : '다음'

  return (
    <Layout
      title="내 정보 입력"
      showBack
      foot={<button className="mc-primary" form="mc-form" type="submit" disabled={busy}>{label}</button>}
    >
      {manage ? <>
        <div className="mc-eyebrow">내 정보 입력</div>
        <h1>나에게 맞는 예산을<br />만들기 위해 알려주세요</h1>
        <p className="mc-muted">입력한 정보와 소비 우선순위를 바탕으로 예산을 추천해요.</p>
        <form className="mc-form" id="mc-form" onSubmit={onSubmit}>
          <div className="mc-card mc-two">
            {steps.slice(0, 2).map((s) => <div key={s.key}><h2 className="mc-mini-h">{s.label}</h2>{s.body}</div>)}
          </div>
          {steps.slice(2).map((s) => (
            <div className="mc-card" key={s.key}>
              {s.reset !== undefined
                ? <div className="mc-row"><h2>{s.label}{s.optional && <> <span className="mc-opt">선택</span></>}</h2>{s.reset}</div>
                : <h2>{s.label}</h2>}
              {s.body}
            </div>
          ))}
          <div className="mc-error" role="alert">{error}</div>
        </form>
      </> : <>
        <StepBar step={1} />
        <form className="mc-form mc-o3-ask" id="mc-form" onSubmit={onSubmit}>
          {at < last ? (
            <div className="mc-o3-q" key={steps[at].key}>
              <span className="mc-o3-pill">{at + 1} / {last} · {steps[at].label}</span>
              <h1>{steps[at].ask}{steps[at].optional && <span className="mc-opt">선택</span>}</h1>
              {steps[at].reset && <div className="mc-chat-tools">{steps[at].reset}</div>}
              {steps[at].body}
              {at > 0 && <button type="button" className="mc-o3-prev" onClick={() => edit(at - 1)}>‹ 이전 질문</button>}
            </div>
          ) : (
            <div className="mc-o3-q" key="done">
              <span className="mc-o3-pill">마지막 확인</span>
              <h1>이대로 저장할까요?</h1>
              <ul className="mc-o3-review">
                {steps.map((s, i) => (
                  <li key={s.key}>
                    <button type="button" data-step={s.key} onClick={() => edit(i)}><small>{s.label}</small><b>{s.answer}</b><span aria-hidden="true">›</span></button>
                  </li>
                ))}
              </ul>
            </div>
          )}
          <div className="mc-error" role="alert">{error}</div>
        </form>
      </>}
    </Layout>
  )
}
