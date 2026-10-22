import { redirect } from "next/navigation";

/** Plan v3 has no organizations (one account is one company): old links to the organization step go to the console,
 *  which routes the account to setup, its approval, or its products. */
export default function OrganizationPage(): never {
  redirect("/app");
}
