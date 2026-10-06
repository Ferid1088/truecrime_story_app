import { CaseWorkspace } from "@/components/case/workspace";

export default async function CasePage({ params }: PageProps<"/cases/[id]">) {
  const { id } = await params;
  const caseId = Number(id);
  if (!Number.isInteger(caseId) || caseId <= 0) {
    return <p className="text-sm text-muted-foreground">Invalid case id.</p>;
  }
  return <CaseWorkspace caseId={caseId} />;
}
