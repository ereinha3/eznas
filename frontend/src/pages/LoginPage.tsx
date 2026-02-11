import React, { useState, useEffect } from 'react';
import { useAuth } from '../contexts/AuthContext';

interface LoginPageProps {
  onLoginSuccess: () => void;
  defaultCredentials?: { username: string; password: string };
}

export function LoginPage({ onLoginSuccess, defaultCredentials }: LoginPageProps) {
  const [username, setUsername] = useState(defaultCredentials?.username || '');
  const [password, setPassword] = useState(defaultCredentials?.password || '');
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [showDefaultCreds, setShowDefaultCreds] = useState(!!defaultCredentials);
  const { login, isLoading: isAuthLoading } = useAuth();

  // Check for default credentials notification
  useEffect(() => {
    if (defaultCredentials) {
      setShowDefaultCreds(true);
      // Auto-hide after 30 seconds
      const timer = setTimeout(() => setShowDefaultCreds(false), 30000);
      return () => clearTimeout(timer);
    }
  }, [defaultCredentials]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setIsLoading(true);

    try {
      const result = await login(username, password);
      if (result.success) {
        onLoginSuccess();
      } else {
        setError(result.message || 'Invalid username or password');
      }
    } catch {
      setError('An unexpected error occurred. Please try again.');
    } finally {
      setIsLoading(false);
    }
  };

  if (isAuthLoading) {
    return (
      <div className="login-container">
        <div className="login-box">
          <div className="loading">Loading...</div>
        </div>
      </div>
    );
  }

  return (
    <div className="login-container">
      <div className="login-box">
        <div className="login-header">
          <h1>NAS Orchestrator</h1>
          <p className="subtitle">Sign in to manage your media stack</p>
        </div>

        {showDefaultCreds && defaultCredentials && (
          <div className="alert alert-warning">
            <strong>Default credentials created:</strong>
            <div>Username: <code>{defaultCredentials.username}</code></div>
            <div>Password: <code>{defaultCredentials.password}</code></div>
            <div className="alert-note">Please change these after your first login!</div>
          </div>
        )}

        {error && (
          <div className="alert alert-error">
            {error}
          </div>
        )}

        <form onSubmit={handleSubmit} className="login-form">
          <div className="form-group">
            <label htmlFor="username">Username</label>
            <input
              type="text"
              id="username"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              disabled={isLoading}
              autoFocus
              required
            />
          </div>

          <div className="form-group">
            <label htmlFor="password">Password</label>
            <input
              type="password"
              id="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              disabled={isLoading}
              required
            />
          </div>

          <button
            type="submit"
            className="login-button"
            disabled={isLoading || !username || !password}
          >
            {isLoading ? 'Signing in...' : 'Sign In'}
          </button>
        </form>

        <div className="login-footer">
          <p>NAS Orchestrator v0.1.0</p>
        </div>
      </div>

      <style>{`
        .login-container {
          min-height: 100vh;
          display: flex;
          align-items: center;
          justify-content: center;
          background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
          padding: 20px;
        }

        .login-box {
          background: #1e1e2e;
          border-radius: 8px;
          padding: 40px;
          width: 100%;
          max-width: 400px;
          box-shadow: 0 10px 40px rgba(0, 0, 0, 0.3);
        }

        .login-header {
          text-align: center;
          margin-bottom: 30px;
        }

        .login-header h1 {
          color: #fff;
          font-size: 28px;
          margin: 0 0 8px 0;
        }

        .subtitle {
          color: #888;
          margin: 0;
          font-size: 14px;
        }

        .alert {
          padding: 12px 16px;
          border-radius: 6px;
          margin-bottom: 20px;
          font-size: 14px;
        }

        .alert-warning {
          background: #fef3c7;
          border: 1px solid #f59e0b;
          color: #92400e;
        }

        .alert-error {
          background: #fee2e2;
          border: 1px solid #ef4444;
          color: #991b1b;
        }

        .alert code {
          background: rgba(0, 0, 0, 0.1);
          padding: 2px 6px;
          border-radius: 3px;
          font-family: monospace;
          font-weight: bold;
        }

        .alert-note {
          margin-top: 8px;
          font-style: italic;
          font-size: 13px;
        }

        .login-form {
          display: flex;
          flex-direction: column;
          gap: 20px;
        }

        .form-group {
          display: flex;
          flex-direction: column;
          gap: 6px;
        }

        .form-group label {
          color: #aaa;
          font-size: 14px;
          font-weight: 500;
        }

        .form-group input {
          padding: 12px 16px;
          border: 1px solid #333;
          border-radius: 6px;
          background: #2a2a3e;
          color: #fff;
          font-size: 15px;
          transition: border-color 0.2s;
        }

        .form-group input:focus {
          outline: none;
          border-color: #5c9ceb;
        }

        .form-group input:disabled {
          opacity: 0.6;
          cursor: not-allowed;
        }

        .login-button {
          padding: 14px;
          background: #5c9ceb;
          color: white;
          border: none;
          border-radius: 6px;
          font-size: 16px;
          font-weight: 600;
          cursor: pointer;
          transition: background 0.2s;
          margin-top: 10px;
        }

        .login-button:hover:not(:disabled) {
          background: #4a8bd9;
        }

        .login-button:disabled {
          opacity: 0.6;
          cursor: not-allowed;
        }

        .loading {
          text-align: center;
          color: #888;
          padding: 40px;
        }

        .login-footer {
          margin-top: 30px;
          text-align: center;
          color: #666;
          font-size: 12px;
        }
      `}</style>
    </div>
  );
}
