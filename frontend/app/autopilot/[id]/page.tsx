import { RunPage } from "@/components/autopilot/run-page";

export default async function AutopilotRunPage({ params }: PageProps<"/autopilot/[id]">) {
  const { id } = await params;
  const runId = Number(id);
  if (!Number.isInteger(runId) || runId <= 0) {
    return <p className="text-sm text-muted-foreground">Invalid run id.</p>;
  }
  return <RunPage runId={runId} />;
}
