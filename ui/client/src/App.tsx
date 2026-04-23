import { Routes, Route, NavLink, Navigate } from 'react-router-dom';
import { useStatus } from './hooks/useApi';
import { AuditProvider } from './contexts/AuditContext';
import Dashboard from './views/Dashboard';
import AuditRunner from './views/AuditRunner';
import ReportViewer from './views/ReportViewer';
import ProofGraph from './views/ProofGraph';
import Settings from './views/Settings';

function ConnectionBanner({ isError }: { isError: boolean }) {
  if (!isError) return null;
  return (
    <div className="connection-banner">
      ⚠ Cannot reach server — check that <code>ui/start.sh</code> is running
    </div>
  );
}

export default function App() {
  const { isError } = useStatus();

  return (
    <AuditProvider>
      <div className="app">
        <ConnectionBanner isError={isError} />
        <header className="header">
          <div className="header-brand">
            <span className="header-title">Proof Auditor</span>
          </div>
          <nav className="header-nav" aria-label="Main navigation">
            <NavLink to="/" className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`} end aria-label="Run audit">
              Audit
            </NavLink>
            <NavLink to="/dashboard" className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`} aria-label="View history">
              History
            </NavLink>
            <NavLink to="/settings" className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`} aria-label="Configure settings">
              Settings
            </NavLink>
          </nav>
        </header>
        <main className="main-content">
          <Routes>
            <Route path="/" element={<AuditRunner />} />
            <Route path="/dashboard" element={<Dashboard />} />
            <Route path="/report/:name" element={<ReportViewer />} />
            <Route path="/graph/:name" element={<ProofGraph />} />
            <Route path="/settings" element={<Settings />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </main>
      </div>
    </AuditProvider>
  );
}

