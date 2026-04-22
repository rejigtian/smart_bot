import { BrowserRouter, NavLink, Route, Routes } from 'react-router-dom'
import Devices from './pages/Devices'
import Suites from './pages/Suites'
import SuiteDetail from './pages/SuiteDetail'
import Runs from './pages/Runs'
import RunDetail from './pages/RunDetail'
import Settings from './pages/Settings'
import QuickRun from './pages/QuickRun'
import Recorder from './pages/Recorder'
import RunCompare from './pages/RunCompare'

const NAV_LINKS = [
  { to: '/', label: '设备', end: true },
  { to: '/quick', label: '快速任务', end: false },
  { to: '/recorder', label: '录制', end: false },
  { to: '/suites', label: '测试套件', end: false },
  { to: '/runs', label: '运行记录', end: true },
  { to: '/runs/compare', label: '对比', end: false },
  { to: '/settings', label: '设置', end: false },
]

export default function App() {
  return (
    <BrowserRouter>
      <div className="min-h-screen bg-gray-50 text-gray-900">
        <nav className="bg-white border-b shadow-sm">
          <div className="max-w-6xl mx-auto flex items-center gap-6 px-6 h-14">
            <span className="font-bold text-lg tracking-tight">smart-androidbot</span>
            {NAV_LINKS.map(({ to, label, end }) => (
              <NavLink
                key={to}
                to={to}
                end={end}
                className={({ isActive }) =>
                  `text-sm font-medium transition-colors ${
                    isActive ? 'text-blue-600' : 'text-gray-600 hover:text-gray-900'
                  }`
                }
              >
                {label}
              </NavLink>
            ))}
          </div>
        </nav>

        <main className="max-w-6xl mx-auto px-6 py-8">
          <Routes>
            <Route path="/" element={<Devices />} />
            <Route path="/quick" element={<QuickRun />} />
            <Route path="/recorder" element={<Recorder />} />
            <Route path="/suites" element={<Suites />} />
            <Route path="/suites/:suiteId" element={<SuiteDetail />} />
            <Route path="/runs" element={<Runs />} />
            <Route path="/runs/compare" element={<RunCompare />} />
            <Route path="/runs/:runId" element={<RunDetail />} />
            <Route path="/settings" element={<Settings />} />
          </Routes>
        </main>
      </div>
    </BrowserRouter>
  )
}
