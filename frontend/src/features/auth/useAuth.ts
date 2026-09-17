import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { createElement } from "react";
import { login as loginApi, logout as logoutApi, signup as signupApi } from "@/api/authApi";
import { getMyProfile } from "@/api/userApi";
import { ApiError } from "@/api/client";
import type { LoginRequest, SignupRequest, UserProfile } from "@/types/auth";

interface AuthContextValue {
  user: UserProfile | null;
  /** 세션 쿠키 복원 시도가 아직 끝나지 않은 최초 로딩 상태 */
  isLoading: boolean;
  login: (payload: LoginRequest) => Promise<UserProfile>;
  signup: (payload: SignupRequest) => Promise<UserProfile>;
  logout: () => Promise<void>;
  /** API-05 저장 성공 등 다른 화면에서 프로필이 바뀌었을 때 전역 상태 동기화 */
  setUser: (user: UserProfile | null) => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

/**
 * 세션은 HttpOnly 쿠키로만 존재하므로, 새로고침 시 로그인 여부를 알
 * 방법이 API-04 호출뿐이다 — 앱 마운트 시 1회 호출해 복원을 시도한다
 * (401이면 비로그인으로 간주, 조용히 넘어간다).
 */
export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<UserProfile | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    getMyProfile()
      .then((profile) => {
        if (!cancelled) setUser(profile);
      })
      .catch(() => {
        if (!cancelled) setUser(null);
      })
      .finally(() => {
        if (!cancelled) setIsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const login = useCallback(async (payload: LoginRequest) => {
    const summary = await loginApi(payload);
    const profile = await getMyProfile();
    setUser(profile);
    return profile ?? (summary as unknown as UserProfile);
  }, []);

  const signup = useCallback(async (payload: SignupRequest) => {
    await signupApi(payload);
    const profile = await getMyProfile();
    setUser(profile);
    return profile;
  }, []);

  const logout = useCallback(async () => {
    try {
      await logoutApi();
    } catch (err) {
      // API-03 비고: 이미 만료된 세션 재호출은 실패해도 프론트 상태는 정리한다.
      if (!(err instanceof ApiError)) throw err;
    }
    setUser(null);
  }, []);

  const value = useMemo<AuthContextValue>(
    () => ({ user, isLoading, login, signup, logout, setUser }),
    [user, isLoading, login, signup, logout],
  );

  return createElement(AuthContext.Provider, { value }, children);
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth는 AuthProvider 안에서만 사용할 수 있습니다.");
  return ctx;
}
