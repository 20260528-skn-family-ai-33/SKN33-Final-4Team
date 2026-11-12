// 저장된 회원·프로필·급여 정보 조회. 변경은 기존 수정 화면에서만 한다.
import { Navigate, useNavigate } from 'react-router'
import Layout from '../components/Layout.jsx'
import { useApi } from '../utils/api.js'
import { won } from '../utils/money.js'
import { paydayText } from '../utils/days.js'
import { catName } from '../utils/categories.js'
import { HOUSING } from '../utils/direction.js'
import { PATHS } from '../routes.js'

const unset = '미설정'

function InfoRow({ label, children }) {
  return <div className="mc-keyvalue"><span>{label}</span><strong style={{ textAlign: 'right', overflowWrap: 'anywhere', minWidth: 0 }}>{children}</strong></div>
}

export default function MyInfoPage() {
  const navigate = useNavigate()
  const account = useApi('/api/auth/me/')
  const profile = useApi('/api/profile/')
  const salary = useApi('/api/budgets/salary/')
  const queries = [account, profile, salary]
  if (queries.some((query) => query.error?.code === 'not_authenticated')) return <Navigate to={PATHS.login} replace />
  if (queries.some((query) => query.error)) return <Layout title="내 정보 보기" showBack>
    <p className="mc-error" role="alert">저장된 정보를 불러오지 못했어요. 다시 시도해 주세요.</p>
    <button type="button" className="mc-secondary" onClick={() => queries.forEach((query) => query.reload())}>다시 불러오기</button>
  </Layout>
  if (queries.some((query) => !query.data)) return <Layout title="내 정보 보기" showBack><p role="status">저장된 정보를 불러오는 중이에요.</p></Layout>

  const user = account.data
  const info = profile.data
  const pay = salary.data
  const priorities = [...info.priorities].sort((a, b) => a.rank - b.rank)
  const edit = (path) => navigate(path, { state: { from: 'my' } })

  return <Layout title="내 정보 보기" showBack>
    <h1>저장된 내 정보</h1>
    <p className="mc-muted">등록한 정보를 확인할 수 있어요. 변경하려면 각 항목의 수정 버튼을 눌러 주세요.</p>
    <section className="mc-card">
      <h2>회원 정보</h2>
      <InfoRow label="이름">{user.nickname || unset}</InfoRow>
      <InfoRow label="이메일">{user.email || unset}</InfoRow>
    </section>
    <section className="mc-card">
      <div className="mc-row"><h2>내 정보</h2><button type="button" className="mc-link" onClick={() => edit(PATHS.profile)}>내 정보 수정</button></div>
      <InfoRow label="생년월일">{info.birthDate?.replaceAll('-', '.') || unset}</InfoRow>
      <InfoRow label="성별">{{ male: '남성', female: '여성' }[info.gender] ?? unset}</InfoRow>
      <InfoRow label="사는 지역">{info.regionName ?? unset}</InfoRow>
      <InfoRow label="자취 여부">{HOUSING[info.housingType]?.name ?? unset}</InfoRow>
      <InfoRow label="자차 보유">{info.hasCar === true ? '보유' : info.hasCar === false ? '미보유' : unset}</InfoRow>
      <h3>소비 우선순위</h3>
      {priorities.length ? priorities.map((item) => <InfoRow key={item.category} label={`${item.rank}순위`}>{item.categoryName || catName(item.category)}</InfoRow>) : <p className="mc-muted">설정한 우선순위가 없어요.</p>}
      <h3>카테고리 최소 금액</h3>
      {info.categoryMinimums.length ? info.categoryMinimums.map((item) => <InfoRow key={item.category} label={item.categoryName || catName(item.category)}>{won(item.minAmount)}</InfoRow>) : <p className="mc-muted">설정한 최소 금액이 없어요.</p>}
    </section>
    <section className="mc-card">
      <div className="mc-row"><h2>월급·급여일</h2><button type="button" className="mc-link" onClick={() => edit(PATHS.salary)}>월급·급여일 수정</button></div>
      <InfoRow label="월 실수령액">{pay.takeHomeIncome == null ? unset : won(pay.takeHomeIncome)}</InfoRow>
      <InfoRow label="급여일">{pay.payday == null ? unset : `매월 ${paydayText(pay.payday)}`}</InfoRow>
      {pay.pendingPaydayChange && <InfoRow label="급여일 변경 예정">{pay.pendingPaydayChange.effectiveOn}부터 매월 {paydayText(pay.pendingPaydayChange.payday)}</InfoRow>}
      {pay.pendingIncomeChange && <InfoRow label="실수령액 변경 예정">{pay.pendingIncomeChange.effectiveOn}부터 {won(pay.pendingIncomeChange.takeHomeIncome)}</InfoRow>}
    </section>
  </Layout>
}
