// Content for the public homepage (src/app/page.tsx). The notice excerpt quotes CPSC recall 27024 word for word
// (data/samples/cpsc_sample.json, the recall `make seed` loads); a test checks every quoted run against that record.
// The results are the committed evaluation numbers (README "How well it works", evals/results).

export type NoticePart = string | { rule: number; text: string };

export const NOTICE = {
  number: "27024",
  date: "October 15, 2026",
  url: "https://cpsc.gov/Recalls/2027/3M-Company-Recalls-ScotchTM-Thermal-Laminators-Due-to-Risk-of-Serious-Injury-or-Death-from-Fire-Hazard",
  title: "3M Company Recalls Scotch™ Thermal Laminators Due to Risk of Serious Injury or Death from Fire Hazard",
  // "…" marks an omission; each run between omissions is verbatim.
  paragraphs: [
    ["This recall involves the Scotch™ thermal laminator, model number ", { rule: 1, text: "TL1302Z" }, "."],
    [
      "Sold Online At: ",
      { rule: 2, text: "Amazon.com" },
      " from ",
      { rule: 3, text: "June 2024 through September 2026" },
      " for between ",
      { rule: 4, text: "$44 and $56" },
      ".",
    ],
    [
      "…upload a photograph of the laminator with the unplugged ",
      { rule: 5, text: "power cord cut" },
      ". The refund, in the form of a check, will be mailed to the consumer…",
    ],
  ] satisfies NoticePart[][],
};

export const RULES = [
  { rule: 1, name: "Model", value: "TL1302Z" },
  { rule: 2, name: "Sold at", value: "Amazon.com" },
  { rule: 3, name: "Bought between", value: "June 1, 2024 and September 30, 2026" },
  { rule: 4, name: "Refund", value: "$44 to $56, mailed as a check" },
  { rule: 5, name: "Proof", value: "A photo of the laminator with its power cord cut" },
];

export const STEPS = [
  {
    name: "Load the recall",
    text: "Pick a recall from the CPSC archive. Its rules come from the notice, and you review them before the program opens.",
  },
  { name: "Reach owners", text: "Each owner gets a link to one claim, with no account or password." },
  {
    name: "Decide each claim",
    text: "The agent reads the label, the destroyed product, and the receipt, and cites a rule for every reason. A reviewer decides anything unclear.",
  },
  {
    name: "Pay the refund",
    text: "Approved refunds go out as a mailed check or a Stripe transfer, and the ledger refuses to pay a claim twice.",
  },
  {
    name: "File the report",
    text: "The monthly progress report for CPSC is written from the claims and reconciled to the unit.",
  },
];

export const RESULTS = [
  {
    measured: "Rules from 60 recall notices, labeled before either extractor existed",
    result: "91.8% plain extractor, 87.9% AI model",
    meaning: "The plain extractor is more accurate, so the AI model is only a fallback.",
  },
  {
    measured: "Decisions on 300 claims generated from the rules",
    result: "0.982 rules, 0.916 agent",
    meaning: "When claims follow the rules, fixed rules decide them as well as the agent does.",
  },
  {
    measured: "Decisions on 40 claims written blind to the generator",
    result: "0.774 agent, 0.583 rules",
    meaning: "Reading the photos pays off where fixed rules cannot see.",
  },
  {
    measured: "Monthly report against an independent recount",
    result: "0 discrepancies",
    meaning: "Every number in the report traces back to claims.",
  },
  {
    measured: "Model spend to build and evaluate the project",
    result: "$1.63",
    meaning: "A decided claim costs about 0.2 cents in model calls.",
  },
];

export const REPO_URL = "https://github.com/kartikeyamandhar/b2b_agent";
