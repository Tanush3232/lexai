"use client";
import { useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import { authApi } from "@/lib/api";
import { useAuthStore } from "@/lib/stores/auth-store";
import { Loader2, Eye, EyeOff, AlertCircle } from "lucide-react";

export default function LoginPage() {
  const router = useRouter();
  const setAuth = useAuthStore((s) => s.setAuth);

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [loading, setLoading] = useState(false);

  // Errors — cleared ONLY when user re-submits, never on keystroke
  const [error, setError] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<{ email?: string; password?: string }>({});

  const handleLogin = useCallback(async (e?: React.FormEvent) => {
    if (e) e.preventDefault();
    
    // Reset errors on each attempt
    setError(null);
    setFieldErrors({});

    // Client-side validation
    const errs: { email?: string; password?: string } = {};
    if (!email.trim()) {
      errs.email = "Email address is required.";
    } else if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email.trim())) {
      errs.email = "Please enter a valid email address.";
    }
    if (!password) {
      errs.password = "Password is required.";
    }

    if (Object.keys(errs).length > 0) {
      setFieldErrors(errs);
      return;
    }

    setLoading(true);
    try {
      const res = await authApi.login(email.trim(), password);
      // Success!
      setAuth(res.data.user, res.data.access_token);
      router.push("/dashboard");
    } catch (err: any) {
      // The 401 interceptor in api.ts is now fixed to not refresh when on /login
      const detail: string | undefined = err.response?.data?.detail;
      const status: number | undefined = err.response?.status;

      if (status === 401) {
        if (detail?.toLowerCase().includes("account found") || detail?.toLowerCase().includes("email")) {
          setFieldErrors({ email: detail });
        } else if (detail?.toLowerCase().includes("password")) {
          setFieldErrors({ password: detail });
        } else {
          setError(detail || "Incorrect email or password. Please try again.");
        }
      } else if (status === 403) {
        setError(detail || "Your account has been disabled. Contact your administrator.");
      } else if (!err.response) {
        setError("Unable to reach the server. Check your network connection.");
      } else {
        setError(detail || "An unexpected error occurred. Please try again.");
      }
    } finally {
      setLoading(false);
    }
  }, [email, password, router, setAuth]);

  const hasEmailError = !!fieldErrors.email;
  const hasPasswordError = !!fieldErrors.password;

  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        minHeight: "100vh",
        background: "var(--bg)",
      }}
    >
      <div style={{ width: "100%", maxWidth: "420px", padding: "24px 16px" }}>
        {/* Logo */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            gap: "14px",
            marginBottom: "32px",
          }}
        >
          <div
            style={{
              width: "44px",
              height: "44px",
              background: "var(--accent)",
              borderRadius: "12px",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              boxShadow: "0 4px 14px rgba(91,79,207,0.25)",
            }}
          >
            <svg
              viewBox="0 0 24 24"
              fill="none"
              stroke="white"
              strokeWidth="2.2"
              strokeLinecap="round"
              strokeLinejoin="round"
              style={{ width: "22px", height: "22px" }}
            >
              <path d="M12 2L3 7v5c0 5.25 3.75 10.15 9 11.25C17.25 22.15 21 17.25 21 12V7z" />
              <path d="M9 12l2 2 4-4" />
            </svg>
          </div>
          <div>
            <div
              style={{
                fontFamily: "var(--font-playfair), serif",
                fontSize: "24px",
                fontWeight: 700,
                color: "var(--text)",
                lineHeight: 1.1,
                letterSpacing: "-0.01em",
              }}
            >
              LexAI
            </div>
            <div
              style={{
                fontSize: "11px",
                color: "var(--text3)",
                letterSpacing: "0.06em",
                textTransform: "uppercase",
                fontWeight: 500,
              }}
            >
              Legal Operations Platform
            </div>
          </div>
        </div>

        {/* Card */}
        <div
          style={{
            background: "white",
            border: "1px solid var(--border)",
            borderRadius: "16px",
            padding: "36px 36px 32px",
            boxShadow: "0 8px 32px rgba(0,0,0,0.06), 0 1px 4px rgba(0,0,0,0.04)",
          }}
        >
          <h1
            style={{
              fontSize: "18px",
              fontWeight: 700,
              color: "var(--text)",
              margin: "0 0 4px 0",
            }}
          >
            Sign in to your account
          </h1>
          <p style={{ fontSize: "13px", color: "var(--text3)", margin: "0 0 28px 0" }}>
            Use your company email and assigned password.
          </p>

          {/* Global Error Banner */}
          {error && (
            <div
              role="alert"
              style={{
                display: "flex",
                alignItems: "flex-start",
                gap: "10px",
                background: "#fef2f2",
                border: "1px solid #fecaca",
                borderRadius: "10px",
                padding: "12px 14px",
                marginBottom: "20px",
              }}
            >
              <AlertCircle size={16} style={{ color: "#dc2626", flexShrink: 0, marginTop: "1px" }} />
              <p style={{ margin: 0, fontSize: "13px", color: "#dc2626", lineHeight: 1.5 }}>
                {error}
              </p>
            </div>
          )}

          {/* Using a form tag for accessibility but with manual onSubmit handling */}
          <form onSubmit={handleLogin} noValidate style={{ display: "flex", flexDirection: "column", gap: "18px" }}>
            {/* Email */}
            <div>
              <label
                htmlFor="login-email"
                style={{
                  display: "block",
                  fontSize: "13px",
                  fontWeight: 600,
                  color: "var(--text2)",
                  marginBottom: "6px",
                }}
              >
                Email address
              </label>
              <input
                id="login-email"
                type="email"
                autoComplete="email"
                placeholder="you@company.com"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                style={{
                  width: "100%",
                  height: "44px",
                  padding: "0 14px",
                  border: `1.5px solid ${hasEmailError ? "#fca5a5" : "var(--border)"}`,
                  borderRadius: "9px",
                  fontSize: "14px",
                  color: "var(--text)",
                  background: hasEmailError ? "#fff5f5" : "#fdfdfc",
                  fontFamily: "inherit",
                  outline: "none",
                  boxSizing: "border-box",
                  transition: "border-color 0.15s, box-shadow 0.15s",
                }}
                onFocus={(e) => {
                  e.target.style.borderColor = hasEmailError ? "#f87171" : "var(--accent)";
                  e.target.style.boxShadow = hasEmailError
                    ? "0 0 0 3px rgba(220,38,38,0.08)"
                    : "0 0 0 3px rgba(91,79,207,0.08)";
                }}
                onBlur={(e) => {
                  e.target.style.borderColor = hasEmailError ? "#fca5a5" : "var(--border)";
                  e.target.style.boxShadow = "none";
                }}
              />
              {hasEmailError && (
                <p
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: "5px",
                    margin: "6px 0 0",
                    fontSize: "12px",
                    color: "#dc2626",
                  }}
                >
                  <AlertCircle size={12} />
                  {fieldErrors.email}
                </p>
              )}
            </div>

            {/* Password */}
            <div>
              <label
                htmlFor="login-password"
                style={{
                  display: "block",
                  fontSize: "13px",
                  fontWeight: 600,
                  color: "var(--text2)",
                  marginBottom: "6px",
                }}
              >
                Password
              </label>
              <div style={{ position: "relative" }}>
                <input
                  id="login-password"
                  type={showPassword ? "text" : "password"}
                  autoComplete="current-password"
                  placeholder="Enter your password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  style={{
                    width: "100%",
                    height: "44px",
                    padding: "0 48px 0 14px",
                    border: `1.5px solid ${hasPasswordError ? "#fca5a5" : "var(--border)"}`,
                    borderRadius: "9px",
                    fontSize: "14px",
                    color: "var(--text)",
                    background: hasPasswordError ? "#fff5f5" : "#fdfdfc",
                    fontFamily: "inherit",
                    outline: "none",
                    boxSizing: "border-box",
                    transition: "border-color 0.15s, box-shadow 0.15s",
                  }}
                  onFocus={(e) => {
                    e.target.style.borderColor = hasPasswordError ? "#f87171" : "var(--accent)";
                    e.target.style.boxShadow = hasPasswordError
                      ? "0 0 0 3px rgba(220,38,38,0.08)"
                      : "0 0 0 3px rgba(91,79,207,0.08)";
                  }}
                  onBlur={(e) => {
                    e.target.style.borderColor = hasPasswordError ? "#fca5a5" : "var(--border)";
                    e.target.style.boxShadow = "none";
                  }}
                />
                <button
                  type="button"
                  aria-label={showPassword ? "Hide password" : "Show password"}
                  onClick={() => setShowPassword((v) => !v)}
                  style={{
                    position: "absolute",
                    right: 0,
                    top: 0,
                    height: "44px",
                    width: "44px",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    background: "none",
                    border: "none",
                    cursor: "pointer",
                    color: "var(--text3)",
                  }}
                >
                  {showPassword ? <EyeOff size={16} /> : <Eye size={16} />}
                </button>
              </div>
              {hasPasswordError && (
                <p
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: "5px",
                    margin: "6px 0 0",
                    fontSize: "12px",
                    color: "#dc2626",
                  }}
                >
                  <AlertCircle size={12} />
                  {fieldErrors.password}
                </p>
              )}
            </div>

            {/* Submit Button */}
            <button
              type="submit"
              id="btn-login"
              disabled={loading}
              style={{
                width: "100%",
                height: "44px",
                background: loading ? "var(--accent-mid)" : "var(--accent)",
                color: "white",
                border: "none",
                borderRadius: "9px",
                fontFamily: "inherit",
                fontSize: "14px",
                fontWeight: 700,
                cursor: loading ? "not-allowed" : "pointer",
                marginTop: "4px",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                gap: "8px",
                transition: "background 0.15s",
                letterSpacing: "0.01em",
              }}
              onMouseEnter={(e) => {
                if (!loading) e.currentTarget.style.background = "#4a3fbf";
              }}
              onMouseLeave={(e) => {
                if (!loading) e.currentTarget.style.background = "var(--accent)";
              }}
            >
              {loading ? (
                <>
                  <Loader2 size={16} className="spin" />
                  Signing in…
                </>
              ) : (
                "Sign in"
              )}
            </button>
          </form>
        </div>
      </div>
    </div>
  );
}
