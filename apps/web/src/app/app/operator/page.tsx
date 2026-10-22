import { redirect } from "next/navigation";

/** The queue moved to /operator, outside the organization gate; old links keep working. */
export default function OldOperatorPage(): never {
  redirect("/operator");
}
