import { Routes, Route, Navigate } from 'react-router-dom'
import Layout from './components/Layout.tsx'
import RequireSetupGuard from './components/RequireSetupGuard.tsx'
import Login from './pages/Login.tsx'
import SetupWizard from './pages/SetupWizard.tsx'
import Dashboard from './pages/Dashboard.tsx'
import Chat from './pages/Chat.tsx'
import Settings from './pages/Settings.tsx'
import KnowledgeBase from './pages/KnowledgeBase.tsx'
import CronJobs from './pages/CronJobs.tsx'
import SkillStore from './pages/SkillStore.tsx'
import ConnectorStore from './pages/ConnectorStore.tsx'
import Experts from './pages/Experts.tsx'
import ExpertDetail from './pages/ExpertDetail.tsx'
import Channels from './pages/Channels.tsx'
import Plugins from './pages/Plugins.tsx'
import AIModels from './pages/AIModels.tsx'
import { isLoggedIn } from './lib/api.ts'
import { ToastProvider } from './components/ui/Toast.tsx'

function RequireAuth({ children }: { children: React.ReactNode }) {
  if (!isLoggedIn()) return <Navigate to="/login" replace />
  return <>{children}</>
}

export default function App() {
  return (
    <ToastProvider>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="/setup" element={<RequireAuth><SetupWizard /></RequireAuth>} />
        <Route path="/" element={<RequireAuth><RequireSetupGuard><Layout /></RequireSetupGuard></RequireAuth>}>
          <Route index element={<Navigate to="/dashboard" replace />} />
          <Route path="dashboard" element={<Dashboard />} />
          <Route path="dashboard/knowledge" element={<Navigate to="/knowledge" replace />} />
          <Route path="knowledge" element={<KnowledgeBase />} />
          <Route path="skills" element={<SkillStore />} />
          <Route path="connectors" element={<ConnectorStore />} />
          <Route path="experts" element={<Experts />} />
          <Route path="experts/:id" element={<ExpertDetail />} />
          <Route path="channels" element={<Channels />} />
          <Route path="plugins" element={<Plugins />} />
          <Route path="models" element={<AIModels />} />
          <Route path="cron" element={<CronJobs />} />
          <Route path="settings" element={<Settings />} />
          <Route path="chat" element={<Chat />} />
        </Route>
      </Routes>
    </ToastProvider>
  )
}
