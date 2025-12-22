import { useEffect, useState } from 'react';
import { AuthProvider, useAuth } from './contexts/AuthContext';
import { LoginPage } from './pages/LoginPage';
import { HomePage } from './pages/HomePage';
import './App.css';

function AppContent() {
  const { isAuthenticated, isLoading, logout } = useAuth();
  const [syncStarted, setSyncStarted] = useState(false);

  useEffect(() => {
    if (isAuthenticated && !syncStarted) {
      // Start sync when authenticated
      setSyncStarted(true);
      import('./lib/sync').then(({ syncManager }) => {
        syncManager.startSync().catch((err) => {
          console.error('Sync start failed:', err);
        });
      });
    }
  }, [isAuthenticated, syncStarted]);

  if (isLoading) {
    return (
      <div style={{ 
        display: 'flex', 
        alignItems: 'center', 
        justifyContent: 'center', 
        minHeight: '100vh' 
      }}>
        Loading...
      </div>
    );
  }

  if (!isAuthenticated) {
    return <LoginPage />;
  }

  return (
    <div>
      <nav style={{ 
        padding: '10px 20px', 
        borderBottom: '1px solid #ddd',
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'center'
      }}>
        <div style={{ fontWeight: 'bold' }}>ZimX</div>
        <button onClick={logout} style={{
          padding: '8px 16px',
          backgroundColor: '#dc3545',
          color: 'white',
          border: 'none',
          borderRadius: '4px',
          cursor: 'pointer'
        }}>
          Logout
        </button>
      </nav>
      
      <HomePage />
    </div>
  );
}

function App() {
  return (
    <AuthProvider>
      <AppContent />
    </AuthProvider>
  );
}

export default App;

