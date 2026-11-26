import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "./api";
import { go } from "./App";
import { CaseView } from "./CaseView";
import { inView, step, tagClass, typing } from "./lib";
import type { CaseDetail, Session, WorkList } from "./types";

const REFRESH_MS = 4000;
const SEEN = "dd-seen";

function seenSet(): Set<string> {
  try {
    return new Set(JSON.parse(localStorage.getItem(SEEN) ?? "[]") as string[]);
  } catch {
    return new Set();
  }
}

interface Props {
  session: Session;
  reviewer: string;
  query: string;
  caseId: string | null;
  say: (text: string) => void;
  refused: (e: unknown) => string;
}

export function Work({ session, reviewer, query, caseId, say, refused }: Props) {
  const [work, setWork] = useState<WorkList | null>(null);
  const [view, setView] = useState("needs");
  const [detail, setDetail] = useState<CaseDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [seen, setSeen] = useState(seenSet);

  const loadWork = useCallback(async () => {
    try {
      setWork(await api.get<WorkList>("/work"));
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  const loadCase = useCallback(async (id: string, quiet: boolean) => {
    if (!quiet) setLoading(true);
    try {
      setDetail(await api.get<CaseDetail>(`/cases/${encodeURIComponent(id)}`));
    } catch (e) {
      if (!quiet) setDetail(null);
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      if (!quiet) setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadWork();
    const t = window.setInterval(() => void loadWork(), REFRESH_MS);
    return () => window.clearInterval(t);
  }, [loadWork]);

  const shown = useMemo(() => (work ? inView(work.items, view, query) : []), [work, view, query]);
  const ids = shown.map((i) => i.case_id);
  const active = caseId ?? null;

  // Open the first case of the view when none is chosen, as an inbox does.
  useEffect(() => {
    if (!active && ids.length > 0) go("work", ids[0]);
  }, [active, ids]);

  useEffect(() => {
    if (!active) {
      setDetail(null);
      return;
    }
    void loadCase(active, false);
    const t = window.setInterval(() => void loadCase(active, true), REFRESH_MS);
    return () => window.clearInterval(t);
  }, [active, loadCase]);

  // An opened case is read: its unread dot clears (design 4.5).
  useEffect(() => {
    if (!detail) return;
    const key = `${detail.case_id}:${detail.updated_at}`;
    if (seen.has(key)) return;
    const next = new Set(seen).add(key);
    setSeen(next);
    try {
      localStorage.setItem(SEEN, JSON.stringify([...next].slice(-500)));
    } catch {
      /* private mode: dots clear for this page only */
    }
  }, [detail, seen]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (typing(e.target) || e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.key === "j" || e.key === "k") {
        const next = step(ids, active, e.key === "j" ? 1 : -1);
        if (next && next !== active) go("work", next);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [ids, active]);

  const refresh = useCallback(async () => {
    await loadWork();
    if (active) await loadCase(active, true);
  }, [active, loadCase, loadWork]);

  if (work?.no_store) return <div className="gate"><p>{work.no_store}</p></div>;

  const sections = [...new Set((work?.views ?? []).map((v) => v.section))];
  const current = work?.views.find((v) => v.key === view);
  const nextId = active ? step(ids, active, 1) : null;
  const next = nextId && nextId !== active ? shown.find((i) => i.case_id === nextId) ?? null : null;
  const credits = view === "credits" && !query;

  return (
    <div className="work">
      <nav className="rail" aria-label="Views">
        {sections.map((section) => (
          <div key={section}>
            <h2>{section}</h2>
            {work!.views
              .filter((v) => v.section === section)
              .map((v) => (
                <button key={v.key} aria-current={v.key === view && !query} onClick={() => setView(v.key)}>
                  <span>{v.label}</span>
                  <span
                    className={`count${v.key === "unverified" && v.count > 0 ? " red" : ""}${
                      v.key === "replied" && v.count > 0 ? " blue" : ""
                    }`}
                  >
                    {v.count}
                  </span>
                </button>
              ))}
          </div>
        ))}
        <p className="hint">J / K move · A approve · R reject</p>
      </nav>

      <section className="list" aria-label="Cases">
        <div className="list-head">
          <div className="row">
            <h1>{query ? "Search" : current?.label ?? "Cases"}</h1>
            <span className="sort">{query ? `${shown.length} found` : "Most urgent first"}</span>
          </div>
          <p>
            {query
              ? `Every case matching “${query}”.`
              : current?.section === "My work"
                ? "A person is the next step on each of these."
                : current?.section === "Agent has it"
                  ? "The agent has these. No one needs to look."
                  : "Closed cases."}
          </p>
          {credits && shown.length > 0 && (
            <>
              <div className="tape" aria-hidden="true">
                {shown.map((i) => (
                  <span key={i.case_id} className={i.case_id === active ? "active" : ""} />
                ))}
              </div>
              <p className="tape-label">
                {shown.length} code-proven credit{shown.length === 1 ? "" : "s"} · each approved one at a time
              </p>
            </>
          )}
        </div>
        <div className="rows">
          {!work && <div className="empty"><div className="skeleton" style={{ height: 60 }} /></div>}
          {error && <p className="empty error-line">{error}</p>}
          {work && shown.length === 0 && (
            <p className="empty">
              {query ? "No case matches that." : current?.section === "My work" ? "Nothing needs you right now." : "Nothing here."}
            </p>
          )}
          {shown.map((i) => {
            const unread = i.views.includes("replied") && !seen.has(`${i.case_id}:${i.updated_at}`);
            return (
              <button key={i.case_id} className="case-row" aria-current={i.case_id === active} onClick={() => go("work", i.case_id)}>
                <span className="l1">
                  {unread && <span className="dot" aria-label="Unread" />}
                  <span className="seller">{i.seller}</span>
                  <span className={`sla${i.due.at_risk ? " risk" : ""}`}>{i.due.text}</span>
                </span>
                <span className="l2">{i.summary}</span>
                <span className="l3">
                  <span className={`tag ${tagClass(i.tag_tone)}`}>
                    {i.tag}
                    {i.credit ? ` · ${i.credit}` : ""}
                  </span>
                  <span className="num">{i.shipment_ref}</span>
                </span>
              </button>
            );
          })}
        </div>
      </section>

      {active && (loading || !detail) ? (
        <section className="case" aria-busy="true">
          <div className="case-head"><div className="inner"><div className="skeleton" style={{ height: 70, marginBottom: 18 }} /></div></div>
          <div className="case-body"><div className="inner"><div className="skeleton" style={{ height: 220 }} /></div></div>
        </section>
      ) : detail ? (
        <CaseView
          key={detail.case_id}
          detail={detail}
          session={session}
          reviewer={reviewer}
          next={next}
          say={say}
          refused={refused}
          refresh={refresh}
        />
      ) : (
        <section className="case">
          <div className="gate">
            <p className="muted">No case open. Choose one from the list.</p>
          </div>
        </section>
      )}
    </div>
  );
}
