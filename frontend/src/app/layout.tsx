import type { Metadata } from "next";
import { Inter } from "next/font/google";
import "./globals.css";
import { AppShell } from "@/components/AppShell";
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
          <AppShell>{children}</AppShell>
        </OperatorProvider>
      </body>
    </html>
  );
}
