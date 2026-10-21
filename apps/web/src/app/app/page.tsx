import { redirect } from "next/navigation";

// 02-ui.md: /app shows an org switcher for users in several firms (component 4), otherwise the programs list.
export default function ConsoleHome(): never {
  redirect("/app/programs");
}
