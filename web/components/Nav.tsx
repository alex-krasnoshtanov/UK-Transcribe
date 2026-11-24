import Link from "next/link";

import { HealthPill } from "@/components/HealthPill";

export function Nav() {
  return (
    <header className="border-b border-line bg-surface">
      <nav className="mx-auto flex max-w-5xl flex-wrap items-center gap-x-5 gap-y-2 px-4 py-3">
        <Link href="/" className="font-semibold text-ink no-underline">
          UK-Transcribe
        </Link>
        <Link href="/" className="text-ink-2 no-underline hover:text-ink">
          Upload &amp; queue
        </Link>
        <Link href="/history" className="text-ink-2 no-underline hover:text-ink">
          History
        </Link>
        <Link href="/settings" className="text-ink-2 no-underline hover:text-ink">
          Settings
        </Link>
        <span className="ml-auto">
          <HealthPill />
        </span>
      </nav>
    </header>
  );
}
