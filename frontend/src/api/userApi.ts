import { apiClient } from "./client";
import type {
  ChangePasswordRequest,
  ChatDefaults,
  DeleteAccountRequest,
  UpdateProfileRequest,
  UserProfile,
} from "@/types/auth";

/** API-04 GET /api/v1/users/me */
export async function getMyProfile(): Promise<UserProfile> {
  const { data } = await apiClient.get<UserProfile>("/api/v1/users/me");
  return data;
}

/** API-05 PATCH /api/v1/users/me */
export async function updateMyProfile(payload: UpdateProfileRequest): Promise<UserProfile> {
  const { data } = await apiClient.patch<UserProfile>("/api/v1/users/me", payload);
  return data;
}

/** API-06 POST /api/v1/users/me/password */
export async function changePassword(payload: ChangePasswordRequest): Promise<void> {
  await apiClient.post("/api/v1/users/me/password", payload);
}

/** API-07 DELETE /api/v1/users/me */
export async function deleteAccount(payload: DeleteAccountRequest): Promise<void> {
  await apiClient.delete("/api/v1/users/me", { data: payload });
}

/** API-08 GET /api/v1/users/me/chat-defaults */
export async function getChatDefaults(): Promise<ChatDefaults> {
  const { data } = await apiClient.get<ChatDefaults>("/api/v1/users/me/chat-defaults");
  return data;
}
