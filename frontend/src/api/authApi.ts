import { apiClient } from "./client";
import type { LoginRequest, SignupRequest, UserSummary } from "@/types/auth";

/** API-01 POST /api/v1/auth/signup */
export async function signup(payload: SignupRequest): Promise<UserSummary> {
  const { data } = await apiClient.post<{ user: UserSummary }>("/api/v1/auth/signup", payload);
  return data.user;
}

/** API-02 POST /api/v1/auth/login */
export async function login(payload: LoginRequest): Promise<UserSummary> {
  const { data } = await apiClient.post<{ user: UserSummary }>("/api/v1/auth/login", payload);
  return data.user;
}

/** API-03 POST /api/v1/auth/logout */
export async function logout(): Promise<void> {
  await apiClient.post("/api/v1/auth/logout");
}
