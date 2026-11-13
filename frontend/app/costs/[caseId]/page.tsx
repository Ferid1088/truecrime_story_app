import { PageHeader } from "@/components/page-header";
import { CaseCosts } from "@/components/costs/case-costs";

export default async function CaseCostsPage({ params }: PageProps<"/costs/[caseId]">) {
  const { caseId } = await params;
  const id = Number(caseId);
  if (!Number.isInteger(id) || id <= 0) {
    return <p className="text-sm text-muted-foreground">Invalid case id.</p>;
  }
  return (
    <div>
      <PageHeader title="Case costs" description="Steps by language, each vendor, and a click-through from every figure down to the single paid call." />
      <CaseCosts caseId={id} standalone />
    </div>
  );
}
