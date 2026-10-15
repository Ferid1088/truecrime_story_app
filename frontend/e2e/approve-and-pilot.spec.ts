import { expect, test } from "@playwright/test";

// Runs against its own isolated stack (scripts/e2e_approve_pilot_stack.sh):
// a real worker (not a stub pipeline) with the recorded provider, on its own
// disposable database. Not part of the shared e2e_stack.sh job queue.
const apiBase = process.env.PLAYWRIGHT_API_BASE_URL
  ?? `http://127.0.0.1:${process.env.E2E_API_PORT ?? "8012"}`;

type Stage = { name: string; status: string; detail: unknown };
type Job = { id: number; status: string; stages: Stage[] };

async function waitForStage(
  request: import("@playwright/test").APIRequestContext,
  caseId: number,
  stageName: string,
  deadlineMs: number,
): Promise<Stage> {
  const deadline = Date.now() + deadlineMs;
  let last: Stage | undefined;
  while (Date.now() < deadline) {
    const jobs = (await (await request.get(`${apiBase}/api/cases/${caseId}/documentary/jobs`)).json()) as Job[];
    const job = jobs[0];
    last = job?.stages.find((s) => s.name === stageName);
    if (last && last.status !== "pending" && last.status !== "running") return last;
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  if (!last) throw new Error(`stage ${stageName} never appeared for case ${caseId}`);
  return last;
}

test("approve a master story as is, then a pilot skips its critics", async ({ page, request }) => {
  await page.goto("/story-studio?case=1");
  await expect(page.getByText("needs revision").first()).toBeVisible();

  await page.getByRole("button", { name: "Approve as is" }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toContainText("transmitter's disappearance is asserted without a source");
  await dialog.getByRole("button", { name: "Approve as is" }).click();
  await expect(page.getByText("ready for human review")).toBeVisible();
  await expect(page.getByText(/Approved by operator/)).toBeVisible();

  await page.goto("/documentary/1");
  await page.getByRole("spinbutton", { name: "Pilot length (seconds)" }).fill("20");
  await page.getByRole("button", { name: "Start pilot" }).click();

  const approval = await waitForStage(request, 1, "master_approval", 30_000);
  expect(approval.status).toBe("done");
  expect((approval.detail as { manually_approved?: boolean }).manually_approved).toBe(true);
  expect((approval.detail as { approved_by?: string }).approved_by).toBe("operator");
  expect((approval.detail as { redos?: number }).redos).toBe(0);

  const calls = (await (await request.get(`${apiBase}/api/cases/1/agent-runs`)).json()) as Array<{ pipeline_stage: string | null }>;
  expect(calls.filter((c) => c.pipeline_stage === "master_approval")).toHaveLength(0);
});
