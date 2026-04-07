import { useAuthenticate } from "@neondatabase/auth/react/ui";
import { LogOut, Trash2 } from "lucide-react";
import { useState } from "react";
import { useNavigate } from "react-router";
import { toast } from "sonner";

import { authClient } from "#/api/auth";
import { PageHeader } from "#/components/layout/PageHeader";
import { DeleteAccountDialog } from "#/components/shared/DeleteAccountDialog";
import { SectionHeader } from "#/components/shared/SectionHeader";
import { Button } from "#/components/ui/button";
import { Skeleton } from "#/components/ui/skeleton";

function AccountSkeleton() {
  return (
    <div className="space-y-8">
      <div className="space-y-3">
        <Skeleton className="h-3 w-20" />
        <div className="rounded-xl border border-border p-5">
          <div className="flex items-center gap-4">
            <Skeleton className="size-12 rounded-full" />
            <div className="space-y-2">
              <Skeleton className="h-4 w-32" />
              <Skeleton className="h-3 w-48" />
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function UserAvatar({ name, image }: { name: string; image?: string | null }) {
  const initials = name
    .split(" ")
    .map((part) => part[0])
    .join("")
    .slice(0, 2)
    .toUpperCase();

  if (image) {
    return (
      <img
        src={image}
        alt={name}
        className="size-12 rounded-full object-cover"
      />
    );
  }

  return (
    <span
      className="flex size-12 items-center justify-center rounded-full bg-primary/15 font-display text-lg font-semibold text-primary"
      aria-hidden="true"
    >
      {initials}
    </span>
  );
}

export function Account() {
  const navigate = useNavigate();
  const { data: session, isPending } = useAuthenticate();
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [isDeleting, setIsDeleting] = useState(false);

  const user = session?.user;

  const handleSignOut = () => {
    if (!authClient) return;
    authClient.signOut({
      fetchOptions: {
        onSuccess: () => navigate("/auth/sign-in", { replace: true }),
        onError: () => {
          toast.error("Failed to sign out");
        },
      },
    });
  };

  // Neon Auth (Better Auth) must have deleteUser enabled server-side.
  // The endpoint is implied by Neon Auth shipping DeleteAccountCard UI.
  const handleDeleteAccount = async () => {
    if (!authClient) return;
    setIsDeleting(true);
    try {
      await authClient.deleteUser({ fetchOptions: { throw: true } });
      toast.success("Account deleted");
      setDeleteOpen(false);
      navigate("/auth/sign-in", { replace: true });
    } catch (error) {
      toast.error("Failed to delete account", {
        description:
          error instanceof Error
            ? error.message
            : "An unexpected error occurred",
      });
    } finally {
      setIsDeleting(false);
    }
  };

  return (
    <div>
      <title>Account — Mixd</title>
      <PageHeader
        title="Account"
        description="Manage your profile and account settings."
      />

      {isPending && <AccountSkeleton />}

      {!isPending && user && (
        <div className="space-y-12">
          {/* Profile */}
          <div className="space-y-3">
            <SectionHeader title="Profile" />
            <div className="rounded-xl border border-border bg-surface-elevated shadow-elevated p-5">
              <div className="flex items-center justify-between gap-4">
                <div className="flex items-center gap-4">
                  <UserAvatar name={user.name} image={user.image} />
                  <div>
                    <p className="font-display text-sm font-semibold">
                      {user.name}
                    </p>
                    <p className="font-mono text-sm text-text-muted">
                      {user.email}
                    </p>
                  </div>
                </div>
                <Button variant="outline" size="sm" onClick={handleSignOut}>
                  <LogOut className="mr-1.5 size-3.5" />
                  Sign out
                </Button>
              </div>
            </div>
          </div>

          {/* Danger Zone */}
          <div className="space-y-3">
            <SectionHeader
              title="Danger Zone"
              description="Irreversible actions that affect your account."
            />
            <div className="rounded-xl border border-border border-l-2 border-l-destructive/40 bg-surface-elevated shadow-elevated p-5">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <h3 className="font-display text-sm font-semibold text-destructive">
                    Delete account
                  </h3>
                  <p className="mt-0.5 text-sm text-text-muted">
                    Permanently delete your account and all associated data.
                    This cannot be undone.
                  </p>
                </div>
                <Button
                  variant="destructive"
                  size="sm"
                  onClick={() => setDeleteOpen(true)}
                >
                  <Trash2 className="mr-1.5 size-3.5" />
                  Delete account
                </Button>
              </div>
            </div>
          </div>

          <DeleteAccountDialog
            open={deleteOpen}
            onOpenChange={setDeleteOpen}
            user={user}
            isPending={isDeleting}
            onConfirm={handleDeleteAccount}
          />
        </div>
      )}
    </div>
  );
}
