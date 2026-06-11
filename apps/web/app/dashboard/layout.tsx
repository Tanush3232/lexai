"use client";
import { useEffect, useState, useCallback } from "react";
import { useRouter, usePathname } from "next/navigation";
import Link from "next/link";
import { useAuthStore } from "@/lib/stores/auth-store";

const NAV_ITEMS = [
  { 
    href: "/dashboard", 
    label: "Overview", 
    disabled: false,
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
    disabled: false,
    roles: ["legal_team", "reviewer", "ops_admin", "super_admin"],
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
      </svg>
    )
  },
  { 
    href: "/dashboard/tickets", 
    label: "Tickets", 
    disabled: false,
    roles: ["legal_team", "reviewer", "ops_admin", "super_admin"],
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/><path d="M14 2v4a2 2 0 0 0 2 2h4"/><path d="m9 15 2 2 4-4"/>
      </svg>
    )
  },
  { 
    href: "/dashboard/drafts", 
    label: "Contract Drafting", 
    disabled: false,
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
    href: "/dashboard/web-search",
    label: "Web Search",
    disabled: false,
    roles: ["legal_team", "reviewer", "ops_admin", "super_admin"],
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/>
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
    disabled: false,
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
  const [collapsed, setCollapsed] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);

  // Close mobile drawer on route change
  useEffect(() => {
    setMobileOpen(false);
  }, [pathname]);

  // Close on Escape key
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setMobileOpen(false);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  // Lock body scroll when mobile drawer is open
  useEffect(() => {
    document.body.style.overflow = mobileOpen ? "hidden" : "";
    return () => { document.body.style.overflow = ""; };
  }, [mobileOpen]);

  useEffect(() => {
    if (_hasHydrated && !token) {
      router.replace("/login");
    }
  }, [_hasHydrated, token]);

  if (!_hasHydrated) return null;
  if (!token) return null;

  const handleLogout = () => {
    logout();
    router.replace("/login");
  };

  const currentTitle = NAV_ITEMS.find(n => n.href !== "/dashboard" ? pathname.startsWith(n.href) : pathname === n.href)?.label || "Overview";

  const allNavItems = [
    ...NAV_ITEMS,
    ...( ["ops_admin", "super_admin"].includes(user?.role || "") ? [{
      href: "/dashboard/usage",
      label: "Usage Analytics",
      disabled: false,
      roles: ["ops_admin", "super_admin"],
      icon: (
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/>
        </svg>
      )
    }] : [])
  ];

  // Shared nav content (used in both desktop sidebar and mobile drawer)
  const NavContent = ({ onItemClick }: { onItemClick?: () => void }) => (
    <>
      <nav className="sidebar-nav">
        {allNavItems.map((item) => {
          if (user?.role && !(item as any).roles.includes(user.role)) return null;

          const isActive = item.href === "/dashboard"
            ? pathname === "/dashboard"
            : pathname.startsWith(item.href);

          const inner = (
            <>
              {item.icon}
              <span className="nav-label">{item.label}</span>
            </>
          );

          if (item.disabled) {
            return (
              <span
                key={item.href}
                className="nav-item disabled"
                title={collapsed ? item.label : undefined}
              >
                {inner}
              </span>
            );
          }
          return (
            <Link
              key={item.href}
              href={item.href}
              className={`nav-item${isActive ? " active" : ""}`}
              title={collapsed ? item.label : undefined}
              onClick={onItemClick}
            >
              {inner}
            </Link>
          );
        })}
      </nav>

      <div className="sidebar-footer">
        <div className="user-card" title={collapsed ? (user?.full_name || "Admin User") : undefined}>
          <div className="avatar">
            {user?.full_name?.charAt(0).toUpperCase() || "A"}
          </div>
          <div className="user-info">
            <strong>{user?.full_name || "Admin User"}</strong>
            <span>{user?.email?.split("@")[0] || "ops.admin"}</span>
          </div>
        </div>
        <button onClick={handleLogout} className="signout-btn" title={collapsed ? "Sign out" : undefined}>
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/>
          </svg>
          <span className="nav-label">Sign out</span>
        </button>
      </div>
    </>
  );

  return (
    <div className="app-shell">

      {/* ── MOBILE DRAWER OVERLAY ── */}
      {mobileOpen && (
        <div
          className="mobile-overlay"
          onClick={() => setMobileOpen(false)}
          aria-hidden="true"
        />
      )}

      {/* ── MOBILE DRAWER ── */}
      <div className={`mobile-drawer${mobileOpen ? " mobile-drawer-open" : ""}`} role="dialog" aria-modal="true" aria-label="Navigation">
        <div className="mobile-drawer-header">
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
          <button className="mobile-drawer-close" onClick={() => setMobileOpen(false)} aria-label="Close menu">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" width="18" height="18">
              <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
            </svg>
          </button>
        </div>
        <NavContent onItemClick={() => setMobileOpen(false)} />
      </div>

      {/* ── DESKTOP SIDEBAR ── */}
      <div className={`sidebar${collapsed ? " sidebar-collapsed" : ""}`}>
        {/* Collapse toggle — appears on sidebar hover */}
        <button
          className="sidebar-toggle"
          onClick={() => setCollapsed(c => !c)}
          aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
          title={collapsed ? "Expand" : "Collapse"}
        >
          <svg
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2.2"
            strokeLinecap="round"
            strokeLinejoin="round"
            className={`toggle-chevron${collapsed ? " toggle-chevron-collapsed" : ""}`}
          >
            <polyline points="15 18 9 12 15 6" />
          </svg>
        </button>

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

        <NavContent />
      </div>

      {/* Main Container */}
      <div className="main-container">
        {/* Top Navbar */}
        <div className="navbar">
          <div className="navbar-content">
            {/* Mobile hamburger */}
            <button
              className="mobile-hamburger"
              onClick={() => setMobileOpen(true)}
              aria-label="Open navigation"
            >
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" width="18" height="18">
                <line x1="3" y1="6" x2="21" y2="6"/><line x1="3" y1="12" x2="21" y2="12"/><line x1="3" y1="18" x2="21" y2="18"/>
              </svg>
            </button>

            {/* Mobile logo (shown only on mobile) */}
            <div className="mobile-navbar-logo">
              <div className="logo-mark" style={{ width: "22px", height: "22px", borderRadius: "5px" }}>
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M12 2L3 7v5c0 5.25 3.75 10.15 9 11.25C17.25 22.15 21 17.25 21 12V7z"/>
                  <path d="M9 12l2 2 4-4"/>
                </svg>
              </div>
              <span className="mobile-navbar-brand">LexAI</span>
            </div>

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
