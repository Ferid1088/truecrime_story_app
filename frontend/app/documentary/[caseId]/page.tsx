import { DocumentaryWorkspace } from "@/components/documentary/workspace";

export default async function DocumentaryCasePage({ params }: PageProps<"/documentary/[caseId]">) {
  const { caseId } = await params;
  const id = Number(caseId);
  if (!Number.isInteger(id) || id <= 0) {
    return <p className="text-sm text-muted-foreground">Invalid case id.</p>;
  }
  return <DocumentaryWorkspace caseId={id} />;
}
