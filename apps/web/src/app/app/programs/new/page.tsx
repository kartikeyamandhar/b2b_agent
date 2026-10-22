import { redirect } from "next/navigation";

/** Plan v3: a product starts from a recall request under Recall access, which the operator approves; old links to
 *  the retired new-program form land there. */
export default function NewProgramPage(): never {
  redirect("/app/access");
}
