import Layout from '../components/Layout.jsx'
import SavedPolicies from '../components/SavedPolicies.jsx'
import '../styles/policies.css'

export default function SavedPoliciesPage() {
  return <Layout title="저장한 정책" showBack><SavedPolicies /></Layout>
}
