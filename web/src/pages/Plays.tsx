import { PageHeader } from "#/components/layout/PageHeader";
import { PlaysHistoryView } from "#/components/plays/PlaysHistoryView";

export function Plays() {
  return (
    <>
      <PageHeader
        title="Plays"
        description="Every play across your services, newest first."
      />
      <PlaysHistoryView />
    </>
  );
}
