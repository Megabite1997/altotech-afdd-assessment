import { NavLink, Navigate, Route, Routes } from 'react-router-dom'
import Portfolio from './pages/Portfolio'
import Issues from './pages/Issues'
import IssueDetail from './pages/IssueDetail'
import Rules from './pages/Rules'
import RuleDetail from './pages/RuleDetail'
import Equipment from './pages/Equipment'
import Agent from './pages/Agent'
import Health from './pages/Health'

const NAV = [
  { to: '/portfolio', label: 'Portfolio' },
  { to: '/issues', label: 'Issues' },
  { to: '/rules', label: 'Rules' },
  { to: '/agent', label: 'Rule authoring' },
  { to: '/health', label: 'Pipeline health' },
]

export default function App() {
  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <strong>AltoTech</strong>
          <span>Multi-site AFDD operations</span>
        </div>
        <nav>
          {NAV.map((item) => (
            <NavLink key={item.to} to={item.to}
                     className={({ isActive }) => (isActive ? 'nav nav-active' : 'nav')}>
              {item.label}
            </NavLink>
          ))}
          <a className="nav" href="/api/docs" target="_blank" rel="noreferrer">API</a>
        </nav>
      </header>
      <main>
        <Routes>
          <Route path="/" element={<Navigate to="/portfolio" replace />} />
          <Route path="/portfolio" element={<Portfolio />} />
          <Route path="/issues" element={<Issues />} />
          <Route path="/issues/:issueId" element={<IssueDetail />} />
          <Route path="/rules" element={<Rules />} />
          <Route path="/rules/:ruleKey" element={<RuleDetail />} />
          <Route path="/equipment/:equipmentId" element={<Equipment />} />
          <Route path="/agent" element={<Agent />} />
          <Route path="/health" element={<Health />} />
          <Route path="*" element={<p className="state state-empty">Page not found.</p>} />
        </Routes>
      </main>
    </div>
  )
}
