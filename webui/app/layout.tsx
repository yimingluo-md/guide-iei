import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "GUIDE-IEI",
  description: "A clinician-developed, locally run, open-source WES/WGS analysis platform for inborn errors of immunity.",
  icons: {
    icon: "/favicon.svg",
    shortcut: "/favicon.svg",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
