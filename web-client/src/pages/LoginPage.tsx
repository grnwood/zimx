import React, { useState } from 'react';
import { useAuth } from '../contexts/AuthContext';

export const LoginPage: React.FC = () => {
  const { authConfigured, setup, login } = useAuth();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    setLoading(true);

    try {
      if (authConfigured) {
        await login(username, password);
      } else {
        await setup(username, password);
      }
    } catch (err: any) {
      setError(err.message || 'Authentication failed');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{ 
      display: 'flex', 
      flexDirection: 'column', 
      alignItems: 'center', 
      justifyContent: 'center', 
      minHeight: '100vh',
      padding: '20px'
    }}>
      <div style={{ 
        width: '100%', 
        maxWidth: '400px',
        padding: '30px',
        border: '1px solid #ddd',
        borderRadius: '8px'
      }}>
        <h1 style={{ textAlign: 'center', marginBottom: '30px' }}>
          {authConfigured ? 'Login' : 'Setup'} - ZimX
        </h1>
        
        {!authConfigured && (
          <p style={{ marginBottom: '20px', color: '#666' }}>
            Create your admin account to get started.
          </p>
        )}

        <form onSubmit={handleSubmit}>
          <div style={{ marginBottom: '20px' }}>
            <label style={{ display: 'block', marginBottom: '5px' }}>Username</label>
            <input
              type="text"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              required
              minLength={3}
              style={{
                width: '100%',
                padding: '10px',
                fontSize: '16px',
                border: '1px solid #ddd',
                borderRadius: '4px'
              }}
            />
          </div>

          <div style={{ marginBottom: '20px' }}>
            <label style={{ display: 'block', marginBottom: '5px' }}>Password</label>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              minLength={8}
              style={{
                width: '100%',
                padding: '10px',
                fontSize: '16px',
                border: '1px solid #ddd',
                borderRadius: '4px'
              }}
            />
          </div>

          {error && (
            <div style={{ 
              marginBottom: '20px', 
              padding: '10px', 
              backgroundColor: '#fee', 
              color: '#c00',
              borderRadius: '4px'
            }}>
              {error}
            </div>
          )}

          <button
            type="submit"
            disabled={loading}
            style={{
              width: '100%',
              padding: '12px',
              fontSize: '16px',
              backgroundColor: '#007bff',
              color: 'white',
              border: 'none',
              borderRadius: '4px',
              cursor: loading ? 'not-allowed' : 'pointer',
              opacity: loading ? 0.6 : 1
            }}
          >
            {loading ? 'Please wait...' : (authConfigured ? 'Login' : 'Create Account')}
          </button>
        </form>
      </div>
    </div>
  );
};
