// 주소 → 화면 연결. 새 화면은 routes.js 에 주소, 여기 ROUTES 에 한 줄 추가
import { Routes, Route } from 'react-router'
import { PATHS } from './routes.js'
// 하단 탭
import HomePage from './pages/HomePage.jsx'
import TransactionsPage from './pages/TransactionsPage.jsx'
import BudgetPage from './pages/BudgetPage.jsx'
import CushionPage from './pages/CushionPage.jsx'
import AssistantPage from './pages/AssistantPage.jsx'
// 회원
import LoginPage from './pages/LoginPage.jsx'
import SignupPage from './pages/SignupPage.jsx'
import OnboardPage from './pages/OnboardPage.jsx'
import ProfilePage from './pages/ProfilePage.jsx'
import MyPage from './pages/MyPage.jsx'
// 계좌 연결
import ConnectPage from './pages/ConnectPage.jsx'
import ConnectAuthPage from './pages/ConnectAuthPage.jsx'
import ConnectAccountsPage from './pages/ConnectAccountsPage.jsx'
import ConnectCardsPage from './pages/ConnectCardsPage.jsx'
import ConnectDonePage from './pages/ConnectDonePage.jsx'
import LoanCheckPage from './pages/LoanCheckPage.jsx'
import ConnectionsPage from './pages/ConnectionsPage.jsx'
// 첫 예산 설정
import SalaryPage from './pages/SalaryPage.jsx'
import FixedPage from './pages/FixedPage.jsx'
import DirectionPage from './pages/DirectionPage.jsx'
import ProtectPage from './pages/ProtectPage.jsx'
import RecommendPage from './pages/RecommendPage.jsx'
import NotFoundPage from './pages/NotFoundPage.jsx'

const ROUTES = [
  [PATHS.home, HomePage], [PATHS.txs, TransactionsPage], [PATHS.budget, BudgetPage],
  [PATHS.fund, CushionPage], [PATHS.assistant, AssistantPage],
  [PATHS.login, LoginPage], [PATHS.signup, SignupPage], [PATHS.onboard, OnboardPage],
  [PATHS.profile, ProfilePage], [PATHS.my, MyPage],
  [PATHS.connect, ConnectPage], [PATHS.connectAuth, ConnectAuthPage], [PATHS.connectAccounts, ConnectAccountsPage],
  [PATHS.connectCards, ConnectCardsPage], [PATHS.connectDone, ConnectDonePage], [PATHS.loanCheck, LoanCheckPage],
  [PATHS.connections, ConnectionsPage],
  [PATHS.salary, SalaryPage], [PATHS.fixed, FixedPage], [PATHS.direction, DirectionPage],
  [PATHS.protect, ProtectPage], [PATHS.recommend, RecommendPage],
]

export default function App() {
  return (
    <Routes>
      {ROUTES.map(([path, Page]) => <Route key={path} path={path} element={<Page />} />)}
      {/* 위 주소에 없으면 */}
      <Route path="*" element={<NotFoundPage />} />
    </Routes>
  )
}
