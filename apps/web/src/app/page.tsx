import type { Metadata } from "next";
import Link from "next/link";

import { BrandMark } from "@/components/BrandMark";
import { NoticeFigure } from "@/components/home/NoticeFigure";
import { REPO_URL, RESULTS, STEPS } from "@/lib/home-content";

export const metadata: Metadata = {
  title: { absolute: "RecallOps: run a product recall from the CPSC notice to the monthly report" },
  description:
    "RecallOps turns a CPSC recall notice into rules, checks every owner's claim and photos against them, pays each refund once, and writes the monthly progress report.",
};

const WRAP = "mx-auto w-full max-w-6xl px-4 sm:px-6";

/** The public homepage. It loads no Clerk script: sign-in lives on /sign-in and the console under /app. */
export default function Home() {
  return (
    <>
      <a
        href="#main"
        className="sr-only rounded-md bg-accent px-4 py-2 font-semibold text-bg focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-10"
      >
        Skip to content
      </a>
      <header className={`${WRAP} flex items-center justify-between gap-4 py-5`}>
        <Link href="/" className="flex items-center gap-2.5 rounded-md">
          <BrandMark size={30} />
          <span className="text-[1.0625rem] font-semibold tracking-[-0.01em] text-ink">RecallOps</span>
        </Link>
        <nav aria-label="Site" className="flex items-center gap-1 sm:gap-2">
          <a href={REPO_URL} className="rounded-md px-3 py-2 text-sm text-ink-muted hover:text-ink">
            GitHub
          </a>
          <Link
            href="/sign-in"
            className="rounded-md border border-line-strong px-3 py-2 text-sm font-medium text-ink hover:border-ink"
          >
            Sign in
          </Link>
        </nav>
      </header>

      <main id="main">
        <section
          aria-labelledby="hero-title"
          className={`${WRAP} grid gap-10 pb-16 pt-6 lg:grid-cols-[minmax(0,5fr)_minmax(0,7fr)] lg:gap-14 lg:pb-24 lg:pt-12`}
        >
          <div className="min-w-0 lg:pt-4">
            <h1
              id="hero-title"
              className="text-balance text-[2.375rem] font-[650] leading-[1.06] tracking-[-0.03em] text-ink sm:text-[3.25rem]"
            >
              Run a product recall from the CPSC notice to the monthly report.
            </h1>
            <p className="mt-6 max-w-[34rem] text-pretty text-[1.0625rem] leading-[1.65] text-ink-muted">
              RecallOps reads the recall notice and turns it into rules. An AI agent checks claim photos and receipts
              against them, up to a daily limit per product, and a person decides anything unclear. Approved refunds
              are paid once, and the monthly report to CPSC is written from the claims.
            </p>
            <div className="mt-8 flex flex-wrap gap-3">
              <Link
                href="/sign-up"
                className="rounded-md bg-accent px-5 py-3 text-[0.9375rem] font-semibold text-bg hover:brightness-110"
              >
                Create an account
              </Link>
              <Link
                href="/app"
                className="rounded-md border border-line-strong px-5 py-3 text-[0.9375rem] font-semibold text-ink hover:border-ink"
              >
                Open the console
              </Link>
            </div>
            <p className="mt-4 max-w-[34rem] text-sm leading-relaxed text-ink-muted">
              A new account names its company and picks a plan. Ask for your recall from the CPSC archive, and it opens once
              the operator confirms your company issued it.
            </p>
          </div>
          <NoticeFigure />
        </section>

        <section aria-labelledby="steps-title" className="border-t border-line">
          <div className={`${WRAP} py-16 lg:py-20`}>
            <h2 id="steps-title" className="text-[1.5rem] font-[650] tracking-[-0.015em] text-ink">
              What happens to a recall
            </h2>
            <p className="mt-2 max-w-[40rem] text-ink-muted">
              A recalling firm must reach owners, check every claim, deliver the remedy, and report to CPSC each month.
              RecallOps does each step from the same records.
            </p>
            <ol className="mt-10 grid gap-8 lg:grid-cols-5 lg:gap-6">
              {STEPS.map((step, i) => (
                <li key={step.name} className="relative min-w-0 pl-7 lg:pl-0 lg:pt-7">
                  <span aria-hidden="true" className="absolute left-0 top-1.5 size-2.5 rounded-full bg-brand lg:top-0" />
                  {i < STEPS.length - 1 && (
                    <span
                      aria-hidden="true"
                      className="absolute -bottom-[calc(2rem+0.375rem)] left-[4.5px] top-5 w-px bg-line lg:-right-6 lg:bottom-auto lg:left-4 lg:top-[4.5px] lg:h-px lg:w-auto"
                    />
                  )}
                  <h3 className="font-semibold text-ink">{step.name}</h3>
                  <p className="mt-1.5 text-pretty text-[0.9375rem] leading-[1.6] text-ink-muted">{step.text}</p>
                </li>
              ))}
            </ol>
          </div>
        </section>

        <section aria-labelledby="results-title" className="border-t border-line bg-panel">
          <div className={`${WRAP} py-16 lg:py-20`}>
            <h2 id="results-title" className="text-[1.5rem] font-[650] tracking-[-0.015em] text-ink">
              How well it works
            </h2>
            <p className="mt-2 max-w-[44rem] text-ink-muted">
              Measured on real CPSC notices and test claims, with the expected result written down before each run.
              Where plain rules beat the agent, the table says so.
            </p>
            <table className="mt-8 w-full text-left text-[0.9375rem]">
              <thead className="max-md:sr-only">
                <tr className="border-b border-line-strong text-sm text-ink-muted">
                  <th scope="col" className="py-3 pr-6 font-medium">
                    Measured
                  </th>
                  <th scope="col" className="py-3 pr-6 font-medium">
                    Result
                  </th>
                  <th scope="col" className="py-3 font-medium">
                    What it means
                  </th>
                </tr>
              </thead>
              <tbody>
                {RESULTS.map((r) => (
                  <tr key={r.measured} className="border-b border-line max-md:block max-md:py-4">
                    <th scope="row" className="py-4 pr-6 align-top font-medium text-ink max-md:block max-md:py-0">
                      {r.measured}
                    </th>
                    <td className="whitespace-nowrap py-4 pr-6 align-top font-semibold tabular-nums text-ink max-md:block max-md:whitespace-normal max-md:py-1">
                      {r.result}
                    </td>
                    <td className="py-4 align-top text-ink-muted max-md:block max-md:py-0">{r.meaning}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="mt-4 max-w-[44rem] text-sm leading-relaxed text-ink-muted">
              Decision scores are macro F1, from 0 to 1, weighting the four outcomes (approve, deny, ask for more
              information, send to a person) equally. <a href={`${REPO_URL}#how-well-it-works`}>Method and full tables</a>
            </p>
          </div>
        </section>

        <section aria-labelledby="real-title" className="border-t border-line">
          <div className={`${WRAP} grid gap-6 py-16 lg:grid-cols-[minmax(0,5fr)_minmax(0,7fr)] lg:gap-14 lg:py-20`}>
            <h2 id="real-title" className="text-[1.5rem] font-[650] tracking-[-0.015em] text-ink">
              What is real here
            </h2>
            <ul className="space-y-3 text-[1.0625rem] leading-relaxed text-ink">
              <li>Recalls come from the public CPSC feed, word for word.</li>
              <li>Claimants, their claims, and their photos are synthetic. No real person&rsquo;s data is used.</li>
              <li>Payments run in Stripe&rsquo;s test mode, so no real money moves.</li>
            </ul>
          </div>
        </section>
      </main>

      <footer className="border-t border-line">
        <div className={`${WRAP} grid gap-3 py-8 text-sm text-ink-muted md:grid-cols-[1fr_auto] md:gap-x-10`}>
          <p>RecallOps is a class project in the UCLA MSBA program, fall 2026.</p>
          <p>Owners filing a claim: open the product&rsquo;s claim link. No account is needed.</p>
          {/* Last in reading order, so phones show both notes together; placed beside them from md up. */}
          <a
            href={REPO_URL}
            className="whitespace-nowrap text-ink underline decoration-line-strong underline-offset-4 hover:decoration-ink md:col-start-2 md:row-span-2 md:row-start-1 md:self-center"
          >
            Source on GitHub
          </a>
        </div>
      </footer>
    </>
  );
}
