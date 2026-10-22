import { redirect } from "next/navigation";

/** The operator's window is /operator, outside the console; old links keep working. */
export default function OldOperatorPage(): never {
  redirect("/operator");
}
