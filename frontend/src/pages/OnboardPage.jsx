// 시작하기
// 가입 · 로그인 바로 다음 첫 화면이라 뒤로 가기 버튼이 없다 (11/12 피드백: 뒤로 가면 로그인된 채 로그인 화면이 나왔다)
import { useNavigate } from 'react-router'
import Layout from '../components/Layout.jsx'
import { PATHS } from '../routes.js'

const STEPS = [
  '내 정보 · 자취 여부 · 소비 우선순위 입력',
  '계좌·카드·대출 연결로 현재 자금 확인',
  '월급과 고정지출 확인',
  '자취 여부에 맞춰 지킬 돈 나누기',
  '카테고리별 추천 예산 확인',
]

export default function OnboardPage() {
  const navigate = useNavigate()

  return (
    <Layout
      title="시작하기"
      foot={<button type="button" className="mc-primary" onClick={() => navigate(PATHS.profile)}>시작하기</button>}
    >
      <div className="mc-eyebrow">MONEY CUSHION</div>
      <h1>첫 월급부터,<br />흔들리지 않는 돈 관리</h1>
      <div className="mc-hero" style={{ marginTop: 28 }}>
        <span>내 돈을 세 가지로</span>
        <div className="mc-money" style={{ fontSize: 28, color: '#fff' }}>생활비 · 저축 · 쿠션</div>
        <p>나갈 돈을 먼저 챙기고<br />마음 편하게 쓸 돈을 확인하세요.</p>
      </div>
      <div className="mc-stack" style={{ margin: '25px 0' }}>
        {/* 번호 뒤 칸은 넓은 공백(\u3000) */}
        {STEPS.map((s, i) => <p key={s}>{`${String(i + 1).padStart(2, '0')}\u3000${s}`}</p>)}
      </div>
    </Layout>
  )
}
