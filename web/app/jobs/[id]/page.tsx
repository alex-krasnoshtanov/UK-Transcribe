import { notFound } from "next/navigation";

import { JobView } from "@/components/JobView";

export default async function JobPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  if (!/^[0-9a-f]{32}$/.test(id)) notFound();
  return <JobView id={id} />;
}
