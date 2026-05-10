import { NavLink } from 'react-router-dom';
import { ConnectionStatus } from './ConnectionStatus';
import { useWebSocket } from '../hooks/useWebSocket';

export function NavBar() {
  const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const wsUrl = `${wsProtocol}//${window.location.host}/ws/live`;

  const { isConnected } = useWebSocket({ url: wsUrl });

  const linkClass = ({ isActive }: { isActive: boolean }) =>
    `px-3 py-2 rounded-md text-sm font-medium transition-colors ${
      isActive
        ? 'bg-gray-700 text-white'
        : 'text-gray-300 hover:bg-gray-700 hover:text-white'
    }`;

  return (
    <header className="border-b border-gray-800 px-6 py-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-6">
          <div className="flex items-center gap-3">
            <h1 className="text-xl font-bold">IBKR Trading Bot</h1>
            <span className="text-gray-500 text-sm">Dashboard</span>
          </div>
          <nav className="flex items-center gap-1" aria-label="Main navigation">
            <NavLink to="/" className={linkClass} end>
              Overview
            </NavLink>
            <NavLink to="/strategies" className={linkClass}>
              Strategies
            </NavLink>
            <NavLink to="/trades" className={linkClass}>
              Trades
            </NavLink>
          </nav>
        </div>
        <ConnectionStatus isConnected={isConnected} />
      </div>
    </header>
  );
}
