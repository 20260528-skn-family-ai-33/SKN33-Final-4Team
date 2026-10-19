// 주소 → 화면 연결 (장고 config/urls.py 역할)
import { Routes, Route } from 'react-router'
import { PATHS } from './routes.js'
import HomePage from './pages/HomePage.jsx'
import TransactionsPage from './pages/TransactionsPage.jsx'
import BudgetPage from './pages/BudgetPage.jsx'
import CushionPage from './pages/CushionPage.jsx'
import AssistantPage from './pages/AssistantPage.jsx'
import MyPage from './pages/MyPage.jsx'
import NotFoundPage from './pages/NotFoundPage.jsx'

export default function App() {
  return (
    <Routes>
      <Route path={PATHS.home} element={<HomePage />} />
      <Route path={PATHS.txs} element={<TransactionsPage />} />
      <Route path={PATHS.budget} element={<BudgetPage />} />
      <Route path={PATHS.fund} element={<CushionPage />} />
      <Route path={PATHS.assistant} element={<AssistantPage />} />
      <Route path={PATHS.my} element={<MyPage />} />
      {/* 위 주소에 없으면 */}
      <Route path="*" element={<NotFoundPage />} />
    </Routes>
  )
}
