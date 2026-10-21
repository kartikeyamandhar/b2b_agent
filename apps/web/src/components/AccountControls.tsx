"use client";

import { OrganizationSwitcher, UserButton } from "@clerk/nextjs";

/** Organization switcher and user menu (04-login.md: organization switcher in the app shell). */
export function AccountControls() {
  return (
    <div className="flex items-center gap-3">
      <OrganizationSwitcher hidePersonal afterSelectOrganizationUrl="/app/programs" afterCreateOrganizationUrl="/app/programs" />
      <UserButton />
    </div>
  );
}
