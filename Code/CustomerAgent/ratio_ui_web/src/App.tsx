import { Component, type ReactNode } from 'react';
import { BrowserRouter, Routes, Route, Navigate, useLocation } from 'react-router-dom';
import { Sidebar } from './components';
import HomePage from './pages/HomePage';
import SRInsightsPage from './pages/SRInsightsPage';
import FuseLandingPage from './pages/FuseLandingPage';
import FuseStudioPage from './pages/FuseStudioPage';
import AgentDevUIPage from './pages/AgentDevUIPage';
import DevUIEmbedPage from './pages/DevUIEmbedPage';
import ChaLayout from './pages/customer-agent/ChaLayout';
import ChaHomePage from './pages/customer-agent/ChaHomePage';
import ChaScenariosPage from './pages/customer-agent/ChaScenariosPage';
import ChaActivePage from './pages/customer-agent/ChaActivePage';
import ChaLivePage from './pages/customer-agent/ChaLivePage';
import ChaTheatrePage from './pages/customer-agent/ChaTheatrePage';
import ChaHistoryPage from './pages/customer-agent/ChaHistoryPage';
import ChaAgentsPage from './pages/customer-agent/ChaAgentsPage';
import ChaConfigPage from './pages/customer-agent/ChaConfigPage';
import ChaDataPage from './pages/customer-agent/ChaDataPage';
import ChaKnowledgePage from './pages/customer-agent/ChaKnowledgePage';
import ChaFlowExecPage from './pages/customer-agent/ChaFlowExecPage';
import ChaFlowDetailPage from './pages/customer-agent/ChaFlowDetailPage';
import ChaNeuralCanvasV2Page from './pages/customer-agent/ChaNeuralCanvasV2Page';
import { InvestigationFlowPage } from './pages/neeraj-version/InvestigationFlowPage';

class ErrorBoundaryInner extends Component<{ children: ReactNode; location: string }, { error: Error | null }> {
  state: { error: Error | null } = { error: null };
  static getDerivedStateFromError(error: Error) { return { error }; }
  componentDidUpdate(prev: { location: string }) {
    if (prev.location !== this.props.location && this.state.error) this.setState({ error: null });
  }
  render() {
    if (this.state.error) {
      return (
        <div style={{ padding: 32, color: '#dc2626' }}>
          <h2>Page Error</h2>
          <pre style={{ whiteSpace: 'pre-wrap' }}>{this.state.error.message}</pre>
        </div>
      );
    }
    return this.props.children;
  }
}

function ErrorBoundary({ children }: { children: ReactNode }) {
  const location = useLocation();
  return <ErrorBoundaryInner location={location.pathname}>{children}</ErrorBoundaryInner>;
}

function AppLayout() {
  const location = useLocation();
  const isCustomerAgent = location.pathname.startsWith('/customer-agent');
  const isInvestigationFlow = location.pathname.startsWith('/investigation-flow');

  // Investigation Reasoning Flow — standalone dark-themed page
  if (isInvestigationFlow) {
    return (
      <ErrorBoundary>
        <Routes>
          <Route path="/investigation-flow" element={<InvestigationFlowPage />} />
        </Routes>
      </ErrorBoundary>
    );
  }

  // Customer Agent has its own full-screen layout with dark sidebar
  if (isCustomerAgent) {
    return (
      <ErrorBoundary>
        <Routes>
          <Route path="/customer-agent" element={<ChaLayout />}>
            <Route index element={<ChaHomePage />} />
            <Route path="scenarios" element={<ChaScenariosPage />} />
            <Route path="live" element={<ChaLivePage />} />
            <Route path="active" element={<ChaActivePage />} />
            <Route path="theatre" element={<ChaTheatrePage />} />
            <Route path="history" element={<ChaHistoryPage />} />
            <Route path="agents" element={<ChaAgentsPage />} />
            <Route path="config" element={<ChaConfigPage />} />
            <Route path="data" element={<ChaDataPage />} />
            <Route path="knowledge" element={<ChaKnowledgePage />} />
            <Route path="investigation-flow" element={<ChaFlowExecPage />} />
            <Route path="investigation-flow-v2" element={<ChaNeuralCanvasV2Page />} />
            <Route path="investigation-flow/detail" element={<ChaFlowDetailPage />} />
            <Route path="investigation-flow/deep-dive" element={<InvestigationFlowPage />} />
            <Route path="investigation-flow/:xcv" element={<ChaFlowDetailPage />} />
          </Route>
        </Routes>
      </ErrorBoundary>
    );
  }

  return (
    <div className="d-flex" style={{ minHeight: '100vh', fontFamily: "'Segoe UI', system-ui, -apple-system, sans-serif" }}>
      <Sidebar />
      <main className="flex-grow-1 overflow-auto bg-light">
        <ErrorBoundary>
          <Routes>
            <Route path="/" element={<HomePage />} />
            <Route path="/sr-insights" element={<SRInsightsPage />} />
            <Route path="/fuse" element={<FuseLandingPage />} />
            <Route path="/fuse/studio" element={<FuseStudioPage />} />
            <Route path="/agent-devui" element={<AgentDevUIPage />} />
            <Route path="/devui-studio" element={<DevUIEmbedPage />} />
            {/* Legacy route redirect */}
            <Route path="/fuse-studio" element={<Navigate to="/fuse/studio" replace />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </ErrorBoundary>
      </main>
    </div>
  );
}

function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="*" element={<AppLayout />} />
      </Routes>
    </BrowserRouter>
  );
}

export default App;
