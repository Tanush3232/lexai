"use client";
import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useAuthStore } from "@/lib/stores/auth-store";

export default function HomePage() {
  const router = useRouter();
  const { isAuthenticated, _hasHydrated } = useAuthStore();

  useEffect(() => {
    if (_hasHydrated) {
      if (isAuthenticated()) {
        router.replace("/dashboard/acts");
      } else {
        router.replace("/login");
      }
    }
  }, [_hasHydrated, isAuthenticated, router]);

  return (
    <div className="min-h-screen flex items-center justify-center" style={{ background: "var(--bg-primary)" }}>
      <div className="w-8 h-8 rounded-full border-2 border-t-transparent animate-spin"
           style={{ borderColor: "var(--purple-primary)", borderTopColor: "transparent" }} />
    </div>
  );
}
