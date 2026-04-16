import type { Metadata } from "next";
import { DM_Sans, Playfair_Display } from "next/font/google";
import "./globals.css";
import { Providers } from "./providers";
import { Toaster } from "sonner";

const dmSans = DM_Sans({
  subsets: ["latin"],
  variable: "--font-dm-sans",
});

const playfair = Playfair_Display({
  subsets: ["latin"],
  variable: "--font-playfair",
});

export const metadata: Metadata = {
  title: "LexAI — Legal Operations Platform",
  description:
    "AI-powered legal operations: document intelligence, contract drafting, and translation — powered by Gemini.",
  keywords: ["legal AI", "contract drafting", "document intelligence", "legal tech"],
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body className={`${dmSans.variable} ${playfair.variable} font-sans antialiased bg-[#f7f7f5] text-[#1a1916]`}>
        <Providers>
          {children}
          <Toaster
            position="top-right"
            theme="light"
            richColors
            closeButton
            toastOptions={{
              style: {
                background: "#ffffff",
                border: "1px solid #e5e7eb",
                color: "#111827",
              },
            }}
          />
        </Providers>
      </body>
    </html>
  );
}
