import { PageHeader } from "@/components/page-header";
import { CostsOverview } from "@/components/costs/overview";

export default function CostsPage() {
  return (
    <div>
      <PageHeader title="Costs" description="The money really spent on every case: by vendor, step and language. Click a case to open its analysis." />
      <CostsOverview />
    </div>
  );
}
