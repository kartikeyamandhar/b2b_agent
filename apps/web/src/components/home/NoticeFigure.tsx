import { NOTICE, RULES, type NoticePart } from "@/lib/home-content";

/** The homepage's centerpiece: the recall notice in its own words, with each phrase a rule came from underlined and
 *  keyed to that rule. Hovering a rule or a phrase highlights its partner (CSS only, globals.css `.notice-rules`). */
export function NoticeFigure() {
  return (
    <figure className="notice-rules min-w-0 overflow-hidden rounded-xl border border-line bg-surface">
      <div className="border-b border-line px-5 py-3 text-sm text-ink-muted sm:px-7">
        <a href={NOTICE.url}>CPSC recall {NOTICE.number}</a>, {NOTICE.date}
      </div>
      <blockquote cite={NOTICE.url} className="px-5 py-5 sm:px-7 sm:py-6">
        <p className="text-pretty font-semibold leading-snug text-ink">{NOTICE.title}</p>
        {NOTICE.paragraphs.map((parts, i) => (
          <p key={i} className="mt-3 text-[0.9375rem] leading-[1.7] text-ink">
            {parts.map((part, j) => renderPart(part, j))}
          </p>
        ))}
      </blockquote>
      <figcaption className="border-t border-line bg-panel px-5 py-5 sm:px-7">
        <p className="text-sm font-semibold text-ink">Rules every claim is checked against</p>
        <dl className="mt-3 space-y-0.5 text-[0.9375rem]">
          {RULES.map((r) => (
            <div
              key={r.rule}
              data-rule={r.rule}
              className="-mx-2 grid grid-cols-[1.25rem_minmax(0,1fr)] gap-x-2 rounded-md px-2 py-1.5 sm:grid-cols-[1.25rem_8rem_minmax(0,1fr)]"
            >
              <dt className="contents">
                <span className="font-semibold tabular-nums text-brand">{r.rule}</span>
                <span className="text-ink-muted">{r.name}</span>
              </dt>
              <dd className="col-start-2 font-medium text-ink sm:col-start-3">{r.value}</dd>
            </div>
          ))}
        </dl>
        <p className="mt-3 text-sm text-ink-muted">
          Each rule is taken from the notice&rsquo;s own words, so a reviewer can check it against the source.
        </p>
      </figcaption>
    </figure>
  );
}

function renderPart(part: NoticePart, key: number) {
  if (typeof part === "string") return part;
  return (
    <mark key={key} data-rule={part.rule} className="notice-mark">
      {part.text}
      <sup>{part.rule}</sup>
    </mark>
  );
}
