"use client";
import { useEffect } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useAuthStore } from "@/lib/stores/auth-store";

import { Suspense } from "react";

function HomeContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { isAuthenticated, _hasHydrated } = useAuthStore();

  useEffect(() => {
    const code = searchParams.get("code");
    const state = searchParams.get("state");
    const msError = searchParams.get("error");

    // Microsoft SSO redirects back to the root (/) with ?code=... — forward to our handler
    if (code || msError) {
      const params = new URLSearchParams();
      if (code) params.set("code", code);
      if (state) params.set("state", state);
      if (msError) params.set("error", msError);
      const errDesc = searchParams.get("error_description");
      if (errDesc) params.set("error_description", errDesc);
      router.replace(`/auth/microsoft/callback?${params.toString()}`);
      return;
    }

    if (_hasHydrated) {
      if (isAuthenticated()) {
        router.replace("/dashboard/acts");
      } else {
        router.replace("/login");
      }
    }
  }, [_hasHydrated, isAuthenticated, router, searchParams]);

  return (
    <div className="min-h-screen flex items-center justify-center" style={{ background: "var(--bg-primary)" }}>
      <div className="w-8 h-8 rounded-full border-2 border-t-transparent animate-spin"
           style={{ borderColor: "var(--purple-primary)", borderTopColor: "transparent" }} />
    </div>
  );
}

export default function HomePage() {
  return (
    <Suspense fallback={
      <div className="min-h-screen flex items-center justify-center" style={{ background: "var(--bg-primary)" }}>
        <div className="w-8 h-8 rounded-full border-2 border-t-transparent animate-spin"
             style={{ borderColor: "var(--purple-primary)", borderTopColor: "transparent" }} />
      </div>
    }>
      <HomeContent />
    </Suspense>
  );
}
