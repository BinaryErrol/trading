import { Outlet } from 'react-router-dom';
import { NavBar } from './NavBar';

export function Layout() {
  return (
    <div className="min-h-screen bg-gray-900 text-white">
      <NavBar />
      <Outlet />
    </div>
  );
}
