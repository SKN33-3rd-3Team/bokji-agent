import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  changePassword as changePasswordApi,
  deleteAccount as deleteAccountApi,
  getMyProfile,
  updateMyProfile,
} from "@/api/userApi";
import { useAuth } from "./useAuth";
import type { ChangePasswordRequest, DeleteAccountRequest, UpdateProfileRequest } from "@/types/auth";

const PROFILE_QUERY_KEY = ["me", "profile"];

/** API-04 — S-09 마이페이지 조회 */
export function useProfile() {
  return useQuery({
    queryKey: PROFILE_QUERY_KEY,
    queryFn: getMyProfile,
  });
}

/** API-05 — 기본 정보 수정(S09-04) */
export function useUpdateProfile() {
  const queryClient = useQueryClient();
  const { setUser } = useAuth();
  return useMutation({
    mutationFn: (payload: UpdateProfileRequest) => updateMyProfile(payload),
    onSuccess: (profile) => {
      queryClient.setQueryData(PROFILE_QUERY_KEY, profile);
      setUser(profile);
      // ChatPage/HomePage가 쓰는 API-08(known_*)은 staleTime: Infinity라
      // 한 번 불러오면 자동으로 다시 안 불러온다. 여기서 무효화하지 않으면
      // 마이페이지에서 정보를 바꿔도 이미 홈/채팅을 한 번이라도 방문한
      // 세션에서는 다음에 또 방문했을 때 예전 known_*로 검색된다.
      queryClient.invalidateQueries({ queryKey: ["chat-defaults"] });
    },
  });
}

/** API-06 — 비밀번호 변경(S09-05) */
export function useChangePassword() {
  return useMutation({
    mutationFn: (payload: ChangePasswordRequest) => changePasswordApi(payload),
  });
}

/**
 * API-07 — 회원 탈퇴(S09-06).
 * 성공해도 여기서 바로 setUser(null)을 하지 않는다 — 그러면 RequireAuth가
 * user를 잃은 걸 보고 완료 팝업이 뜨기도 전에 /login으로 튕겨버린다.
 * 로그아웃 처리는 호출부(MyPage)가 완료 팝업의 "확인" 클릭 시점에 한다.
 */
export function useDeleteAccount() {
  return useMutation({
    mutationFn: (payload: DeleteAccountRequest) => deleteAccountApi(payload),
  });
}
