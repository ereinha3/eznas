import React, { createContext, useContext, useState, useCallback, useEffect } from 'react';

export type UserRole = 'admin' | 'viewer';

export interface User {
  username: string;
  role: UserRole;
}

export interface AuthState {
  isAuthenticated: boolean;
  user: User | null;
  token: string | null;
  isLoading: boolean;
  sudoActive: boolean;
  sudoExpiresAt: Date | null;
}

export interface AuthContextType extends AuthState {
  login: (username: string, password: string) => Promise<{ success: boolean; message?: string }>;
  logout: () => Promise<void>;
  checkSession: () => Promise<boolean>;
  verifySudo: (password: string) => Promise<{ success: boolean; message?: string }>;
  requireSudo: () => Promise<boolean>;
  getAuthHeaders: () => Record<string, string>;
}

const AuthContext = createContext<AuthContextType | null>(null);

const TOKEN_KEY = 'nas_orchestrator_token';

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<AuthState>({
    isAuthenticated: false,
    user: null,
    token: null,
    isLoading: true,
    sudoActive: false,
    sudoExpiresAt: null,
  });

  // Get auth headers for API calls
  const getAuthHeaders = useCallback((): Record<string, string> => {
    if (!state.token) return {};
    return {
      'Authorization': `Bearer ${state.token}`,
    };
  }, [state.token]);

  // Check session validity
  const checkSession = useCallback(async (): Promise<boolean> => {
    const token = localStorage.getItem(TOKEN_KEY);
    if (!token) {
      setState(prev => ({ ...prev, isLoading: false }));
      return false;
    }

    try {
      const response = await fetch('/api/auth/session', {
        headers: {
          'Authorization': `Bearer ${token}`,
        },
      });

      if (response.ok) {
        const data = await response.json();
        setState({
          isAuthenticated: true,
          user: data.username ? { username: data.username, role: data.role } : null,
          token,
          isLoading: false,
          sudoActive: data.sudo_active || false,
          sudoExpiresAt: null,
        });
        return true;
      } else {
        // Token invalid, clear it
        localStorage.removeItem(TOKEN_KEY);
        setState({
          isAuthenticated: false,
          user: null,
          token: null,
          isLoading: false,
          sudoActive: false,
          sudoExpiresAt: null,
        });
        return false;
      }
    } catch (error) {
      console.error('Session check failed:', error);
      setState(prev => ({ ...prev, isLoading: false }));
      return false;
    }
  }, []);

  // Login
  const login = useCallback(async (
    username: string,
    password: string
  ): Promise<{ success: boolean; message?: string }> => {
    try {
      const response = await fetch('/api/auth/login', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ username, password }),
      });

      const data = await response.json();

      if (response.ok && data.success) {
        localStorage.setItem(TOKEN_KEY, data.token);
        setState({
          isAuthenticated: true,
          user: { username: data.username, role: data.role },
          token: data.token,
          isLoading: false,
          sudoActive: false,
          sudoExpiresAt: null,
        });
        return { success: true };
      } else {
        return { success: false, message: data.message || 'Login failed' };
      }
    } catch (error) {
      return { success: false, message: 'Network error. Please try again.' };
    }
  }, []);

  // Logout
  const logout = useCallback(async (): Promise<void> => {
    const token = localStorage.getItem(TOKEN_KEY);
    
    if (token) {
      try {
        await fetch('/api/auth/logout', {
          method: 'POST',
          headers: {
            'Authorization': `Bearer ${token}`,
          },
        });
      } catch (error) {
        console.error('Logout failed:', error);
      }
    }

    localStorage.removeItem(TOKEN_KEY);
    setState({
      isAuthenticated: false,
      user: null,
      token: null,
      isLoading: false,
      sudoActive: false,
      sudoExpiresAt: null,
    });
  }, []);

  // Verify sudo mode
  const verifySudo = useCallback(async (
    password: string
  ): Promise<{ success: boolean; message?: string }> => {
    const token = localStorage.getItem(TOKEN_KEY);
    if (!token) {
      return { success: false, message: 'Not authenticated' };
    }

    try {
      const response = await fetch('/api/auth/sudo', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${token}`,
        },
        body: JSON.stringify({ password }),
      });

      const data = await response.json();

      if (response.ok && data.success) {
        setState(prev => ({
          ...prev,
          sudoActive: true,
          sudoExpiresAt: new Date(Date.now() + 10 * 60 * 1000), // 10 minutes
        }));
        return { success: true };
      } else {
        return { success: false, message: data.message || 'Verification failed' };
      }
    } catch (error) {
      return { success: false, message: 'Network error. Please try again.' };
    }
  }, []);

  // Check if sudo is required and prompt if needed
  const requireSudo = useCallback(async (): Promise<boolean> => {
    if (state.sudoActive && state.sudoExpiresAt && state.sudoExpiresAt > new Date()) {
      return true;
    }
    return false;
  }, [state.sudoActive, state.sudoExpiresAt]);

  // Check session on mount
  useEffect(() => {
    checkSession();
  }, [checkSession]);

  const value: AuthContextType = {
    ...state,
    login,
    logout,
    checkSession,
    verifySudo,
    requireSudo,
    getAuthHeaders,
  };

  return (
    <AuthContext.Provider value={value}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return context;
}
