import { Navigate, Route, Routes } from "react-router-dom";
import { LoginPage } from "@/pages/LoginPage";
import { SignupPage } from "@/pages/SignupPage";
import { ChatPage } from "@/pages/ChatPage";
import { MyPage } from "@/pages/MyPage";
import { useAuth } from "@/features/auth/useAuth";

/**
 * S01-04: 비로그인 상태에서 상담/마이페이지 접근 시 로그인 화면으로
 * 유도한다(제품 정책상 상담은 로그인 필수 — API-10 비고 동일 정책).
 */
function RequireAuth({ children }: { children: React.ReactElement }) {
  const { user, isLoading } = useAuth();
  if (isLoading) return null;
  if (!user) return <Navigate to="/login" replace />;
  return children;
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/signup" element={<SignupPage />} />
      <Route
        path="/chat"
        element={
          <RequireAuth>
            <ChatPage />
          </RequireAuth>
        }
      />
      <Route
        path="/mypage"
        element={
          <RequireAuth>
            <MyPage />
          </RequireAuth>
        }
      />
      <Route path="*" element={<Navigate to="/login" replace />} />
    </Routes>
  );
}
