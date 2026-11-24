import type { Metadata } from "next";
import { connection } from "next/server";

import { Nav } from "@/components/Nav";

import "./globals.css";

export const metadata: Metadata = {
  title: "UK-Transcribe",
  description: "Ukrainian transcription: upload, queue, history and transcripts.",
  robots: { index: false, follow: false },
};

export default async function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  // Rendered per request so every page's scripts carry this request's CSP nonce.
  await connection();
  return (
    <html lang="en">
      <body className="min-h-screen">
        <Nav />
        <main className="mx-auto max-w-5xl px-4 pb-16 pt-6">{children}</main>
      </body>
    </html>
  );
}
