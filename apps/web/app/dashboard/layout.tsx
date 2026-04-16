"use client";
import { useEffect } from "react";
import { useRouter, usePathname } from "next/navigation";
import Link from "next/link";
import { useAuthStore } from "@/lib/stores/auth-store";

const NAV_ITEMS = [
  { 
    href: "/dashboard", 
    label: "Overview", 
    disabled: true,
    roles: ["legal_team", "reviewer", "ops_admin", "super_admin"],
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/>
      </svg>
    )
  },
  { 
    href: "/dashboard/folders", 
    label: "Vault", 
    disabled: false,
    roles: ["legal_team", "reviewer", "ops_admin", "super_admin"],
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>
      </svg>
    )
  },
  { 
    href: "/dashboard/intelligence", 
    label: "Document Intelligence", 
    disabled: true,
    roles: ["legal_team", "reviewer", "ops_admin", "super_admin"],
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
      </svg>
    )
  },
  { 
    href: "/dashboard/drafts", 
    label: "Contract Drafting", 
    disabled: true,
    roles: ["legal_team", "reviewer", "ops_admin", "super_admin"],
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/><polyline points="10 9 9 9 8 9"/>
      </svg>
    )
  },
  {
    href: "/dashboard/acts",
    label: "Legal Acts",
    disabled: false,
    roles: ["legal_team", "reviewer", "ops_admin", "super_admin"],
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/>
      </svg>
    )
  },
  { 
    href: "/dashboard/translations", 
    label: "Translations", 
    disabled: false,
    roles: ["legal_team", "reviewer", "ops_admin", "super_admin"],
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M5 8l6 6"/><path d="M4 14l6-6 2-3"/><path d="M2 5h12"/><path d="M7 2h1"/><path d="M22 22l-5-10-5 10"/><path d="M14 18h6"/>
      </svg>
    )
  },
  { 
    href: "/dashboard/users", 
    label: "User Management", 
    disabled: false,
    roles: ["ops_admin", "super_admin"],
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/>
      </svg>
    )
  },
  { 
    href: "/dashboard/audit", 
    label: "Audit Log", 
    disabled: true,
    roles: ["ops_admin", "super_admin"],
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/><polyline points="10 9 9 9 8 9"/>
      </svg>
    )
  },
];


export default function DashboardLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const router = useRouter();
  const pathname = usePathname();
  const { user, token, logout, _hasHydrated } = useAuthStore();

  useEffect(() => {
    if (_hasHydrated && !token) {
      router.replace("/login");
    }
  }, [_hasHydrated, token]);

  if (!_hasHydrated) return null; // Show nothing or a skeleton until hydrated
  if (!token) return null; // Protect during transition

  const handleLogout = () => {
    logout();
    router.replace("/login");
  };

  const currentTitle = NAV_ITEMS.find(n => n.href !== "/dashboard" ? pathname.startsWith(n.href) : pathname === n.href)?.label || "Overview";

  return (
    <div className="app-shell">
      {/* Sidebar */}
      <div className="sidebar">
        <div className="sidebar-header">
          <div className="navbar-logo">
            <div className="logo-mark">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M12 2L3 7v5c0 5.25 3.75 10.15 9 11.25C17.25 22.15 21 17.25 21 12V7z"/>
                <path d="M9 12l2 2 4-4"/>
              </svg>
            </div>
            <div className="logo-text">
              <strong>LexAI</strong>
              <span>Legal Ops</span>
            </div>
          </div>
        </div>

        <nav className="sidebar-nav">
          {NAV_ITEMS.map((item) => {
            // RBAC: Check if current user role is allowed to see this item
            if (user?.role && !(item as any).roles.includes(user.role)) {
              return null;
            }

            const isActive = item.href === "/dashboard"
              ? pathname === "/dashboard"
              : pathname.startsWith(item.href);
            
            if (item.disabled) {
              return (
                <span
                  key={item.href}
                  className="nav-item disabled"
                  title="Coming soon"
                >
                  {item.icon}
                  {item.label}
                </span>
              );
            }
            return (
              <Link
                key={item.href}
                href={item.href}
                className={`nav-item ${isActive ? "active" : ""}`}
              >
                {item.icon}
                {item.label}
              </Link>
            );
          })}
          {["ops_admin", "super_admin"].includes(user?.role || "") && (
            <Link
              href="/dashboard/usage"
              className={`nav-item ${pathname.startsWith("/dashboard/usage") ? "active" : ""}`}
            >
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/>
              </svg>
              Usage Analytics
            </Link>
          )}
        </nav>

        <div className="sidebar-footer">
          <div className="user-card">
            <div className="avatar">
              {user?.full_name?.charAt(0).toUpperCase() || "A"}
            </div>
            <div className="user-info">
              <strong>{user?.full_name || "Admin User"}</strong>
              <span>{user?.email?.split("@")[0] || "ops.admin"}</span>
            </div>
          </div>
          <button onClick={handleLogout} className="signout-btn">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/>
            </svg>
            Sign out
          </button>
        </div>
      </div>

      {/* Main Container */}
      <div className="main-container">
        {/* Top Navbar */}
        <div className="navbar">
          <div className="navbar-content">
            <span className="navbar-title">{currentTitle}</span>
            <div className="navbar-actions">
              <div className="icon-btn">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ width: "18px", height: "18px" }}>
                  <path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 0 1-3.46 0"/>
                </svg>
              </div>
            </div>
          </div>
        </div>

        {/* Content Area */}
        <div className="main">
          <div className="page-content">
            {children}
          </div>
        </div>
      </div>
    </div>
  );
}
