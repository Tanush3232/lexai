"use client";
import { useEffect, useState, useRef, Suspense } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { authApi } from "@/lib/api";
import { useAuthStore } from "@/lib/stores/auth-store";
import { Loader2, AlertCircle } from "lucide-react";

/**
 * Microsoft SSO Callback Page
 *
 * Microsoft redirects here after the user authenticates.
 * URL will contain: ?code=...&state=...
 *
 * This page:
 *  1. Reads the `code` from the URL
 *  2. Sends it to our backend, which exchanges it with Microsoft for an ID token
 *  3. Backend validates the email exists in LexAI (no new user creation)
 *  4. Receives a LexAI JWT and stores it → redirects to /dashboard
 */
function MicrosoftCallbackContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const setAuth = useAuthStore((s) => s.setAuth);

  const [status, setStatus] = useState<"loading" | "error">("loading");
  const [errorMessage, setErrorMessage] = useState<string>("");
  const hasExchanged = useRef(false);

  useEffect(() => {
    const code = searchParams.get("code");
    const state = searchParams.get("state");
    const msError = searchParams.get("error");
    const msErrorDesc = searchParams.get("error_description");

    // Microsoft returned an error (e.g. user cancelled)
    if (msError) {
      setErrorMessage(
        msErrorDesc
          ? decodeURIComponent(msErrorDesc.replace(/\+/g, " "))
          : "Microsoft sign-in was cancelled or failed."
      );
      setStatus("error");
      return;
    }

    if (!code) {
      setErrorMessage("No authorisation code received from Microsoft.");
      setStatus("error");
      return;
    }

    if (hasExchanged.current) return;
    hasExchanged.current = true;

    // Exchange code for LexAI JWT via our backend
    authApi
      .microsoftCallback(code, state ?? undefined)
      .then((res) => {
        setAuth(res.data.user, res.data.access_token);
        router.replace("/dashboard");
      })
      .catch((err) => {
        const detail: string | undefined = err.response?.data?.detail;
        setErrorMessage(
          detail ||
            "Sign-in failed. Your Microsoft account may not be provisioned in LexAI. Contact your administrator."
        );
        setStatus("error");
      });
  }, [searchParams, setAuth, router]);


  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        minHeight: "100vh",
        background: "var(--bg)",
        fontFamily: "inherit",
      }}
    >
      <div style={{ width: "100%", maxWidth: "400px", padding: "24px 16px", textAlign: "center" }}>
        {/* LexAI logo */}
        <div
          style={{
            width: "48px",
            height: "48px",
            background: "var(--accent)",
            borderRadius: "14px",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            margin: "0 auto 24px",
            boxShadow: "0 4px 14px rgba(91,79,207,0.25)",
          }}
        >
          <svg viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" style={{ width: "22px", height: "22px" }}>
            <path d="M12 2L3 7v5c0 5.25 3.75 10.15 9 11.25C17.25 22.15 21 17.25 21 12V7z" />
            <path d="M9 12l2 2 4-4" />
          </svg>
        </div>

        {status === "loading" && (
          <div
            style={{
              background: "white",
              border: "1px solid var(--border)",
              borderRadius: "16px",
              padding: "40px 32px",
              boxShadow: "0 8px 32px rgba(0,0,0,0.06)",
            }}
          >
            <Loader2
              size={32}
              className="spin"
              style={{ color: "var(--accent)", margin: "0 auto 16px", display: "block" }}
            />
            <p style={{ fontSize: "16px", fontWeight: 600, color: "var(--text)", margin: "0 0 6px" }}>
              Signing you in…
            </p>
            <p style={{ fontSize: "13px", color: "var(--text3)", margin: 0 }}>
              Verifying your Microsoft account with LexAI
            </p>
          </div>
        )}

        {status === "error" && (
          <div
            style={{
              background: "white",
              border: "1px solid var(--border)",
              borderRadius: "16px",
              padding: "40px 32px",
              boxShadow: "0 8px 32px rgba(0,0,0,0.06)",
            }}
          >
            <div
              style={{
                width: "48px",
                height: "48px",
                background: "#fef2f2",
                borderRadius: "50%",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                margin: "0 auto 16px",
              }}
            >
              <AlertCircle size={24} style={{ color: "#dc2626" }} />
            </div>
            <p style={{ fontSize: "16px", fontWeight: 700, color: "var(--text)", margin: "0 0 10px" }}>
              Sign-in failed
            </p>
            <p
              style={{
                fontSize: "13px",
                color: "#dc2626",
                margin: "0 0 24px",
                lineHeight: 1.6,
                background: "#fef2f2",
                border: "1px solid #fecaca",
                borderRadius: "8px",
                padding: "12px",
              }}
            >
              {errorMessage}
            </p>
            <button
              onClick={() => router.replace("/login")}
              style={{
                width: "100%",
                height: "42px",
                background: "var(--accent)",
                color: "white",
                border: "none",
                borderRadius: "9px",
                fontFamily: "inherit",
                fontSize: "14px",
                fontWeight: 700,
                cursor: "pointer",
              }}
            >
              Back to Login
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

export default function MicrosoftCallbackPage() {
  return (
    <Suspense fallback={
      <div style={{ display: "flex", justifyContent: "center", alignItems: "center", minHeight: "100vh", background: "var(--bg)" }}>
        <Loader2 size={32} className="spin" style={{ color: "var(--accent)" }} />
      </div>
    }>
      <MicrosoftCallbackContent />
    </Suspense>
  );
}
