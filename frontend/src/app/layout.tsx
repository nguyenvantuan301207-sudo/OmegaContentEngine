import type { Metadata } from "next";
import { Inter } from "next/font/google";
import "./globals.css";
import { Sidebar } from "@/components/Sidebar";
import { TopBar } from "@/components/TopBar";
import { OperatorProvider } from "@/lib/operator-context";

const inter = Inter({ subsets: ["latin"] });

export const metadata: Metadata = {
  title: "OMEGA — Autonomous Content Operating System",
  description: "OMEGA operator console for autonomous content lifecycle management.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className={inter.className}>
      <body>
        <OperatorProvider>
          <div className="app-shell">
            <Sidebar />
            <div className="app-main-wrapper">
              <TopBar />
              <main className="app-content">{children}</main>
            </div>
          </div>
        </OperatorProvider>
      </body>
    </html>
  );
}
