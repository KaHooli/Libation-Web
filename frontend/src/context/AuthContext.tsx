import React, { createContext, useContext, useEffect, useState, useCallback, useRef } from "react";
import { authApi, setAccessToken } from "@/lib/api";

interface User {
  id: number;
  username: string;
  totp_enabled: boolean;
  is_admin: boolean;
  audible_account_id: string | null;
  download_cap: number | null;
  permissions: Record<string, boolean> | null;
  /** Signs in through the identity provider, so password and 2FA controls
   *  do not apply to this account. */
  is_sso_user?: boolean;
}

interface AuthContextValue {
  user: User | null;
  isLoading: boolean;
  isAuthenticated: boolean;
  login: (accessToken: string, user: User) => void;
  logout: () => Promise<void>;
  refreshUser: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

const ACCESS_TOKEN_REFRESH_MS = 13 * 60 * 1000; // refresh 2 min before 15-min expiry

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const refreshTimer = useRef<ReturnType<typeof setInterval> | null>(null);

  const scheduleRefresh = useCallback(() => {
    if (refreshTimer.current) clearInterval(refreshTimer.current);
    refreshTimer.current = setInterval(async () => {
      try {
        const { data } = await authApi.refresh();
        setAccessToken(data.access_token);
      } catch {
        setUser(null);
        setAccessToken(null);
      }
    }, ACCESS_TOKEN_REFRESH_MS);
  }, []);

  const login = useCallback((accessToken: string, userData: User) => {
    setAccessToken(accessToken);
    setUser(userData);
    scheduleRefresh();
  }, [scheduleRefresh]);

  const logout = useCallback(async () => {
    try { await authApi.logout(); } catch { /* ignore */ }
    setAccessToken(null);
    setUser(null);
    if (refreshTimer.current) clearInterval(refreshTimer.current);
  }, []);

  const refreshUser = useCallback(async () => {
    const { data } = await authApi.me();
    setUser(data);
  }, []);

  // On mount: attempt silent token refresh using the httpOnly cookie
  useEffect(() => {
    authApi.refresh()
      .then(({ data }) => {
        setAccessToken(data.access_token);
        setUser(data.user);
        scheduleRefresh();
      })
      .catch(() => { /* not logged in */ })
      .finally(() => setIsLoading(false));

    return () => { if (refreshTimer.current) clearInterval(refreshTimer.current); };
  }, [scheduleRefresh]);

  return (
    <AuthContext.Provider value={{ user, isLoading, isAuthenticated: !!user, login, logout, refreshUser }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside AuthProvider");
  return ctx;
}
