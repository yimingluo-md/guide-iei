import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "IEI Variant Review",
  description: "Clinical review workbench for VEP-annotated IEI variants.",
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
