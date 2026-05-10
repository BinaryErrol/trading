import { BrowserRouter, Routes, Route } from 'react-router-dom';
import { Layout } from './components/Layout';
import { OverviewPage } from './pages/OverviewPage';
import { StrategyComparisonPage } from './pages/StrategyComparisonPage';
import { StrategyDetailPage } from './pages/StrategyDetailPage';
import { TradeHistoryPage } from './pages/TradeHistoryPage';

function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<Layout />}>
          <Route path="/" element={<OverviewPage />} />
          <Route path="/strategies" element={<StrategyComparisonPage />} />
          <Route path="/strategies/:name" element={<StrategyDetailPage />} />
          <Route path="/trades" element={<TradeHistoryPage />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}

export default App;
