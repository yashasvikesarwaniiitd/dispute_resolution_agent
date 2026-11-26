import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, api, credentials } from "./api";
import { initials } from "./lib";
import { EvalsPage, LedgerPage, PatternsPage, TeamPage } from "./Pages";
import { SellerPage } from "./SellerPage";
import type { Session } from "./types";
import { Work } from "./Work";

export type Page = "work" | "patterns" | "ledger" | "team" | "evals" | "seller";
const PAGES: Page[] = ["work", "patterns", "ledger", "team", "evals", "seller"];

/** `#/work/<case id>` and friends: a reload or a shared link lands on the same place. */
function fromHash(): { page: Page; caseId: string | null } {
  const [, page, caseId] = window.location.hash.split("/");
  return {
    page: PAGES.includes(page as Page) ? (page as Page) : "work",
    caseId: caseId ? decodeURIComponent(caseId) : null,
  };
}

export function go(page: Page, caseId?: string | null): void {
  window.location.hash = `#/${page}${caseId ? `/${encodeURIComponent(caseId)}` : ""}`;
}

export function App() {
  const [route, setRoute] = useState(fromHash);
  const [session, setSession] = useState<Session | null>(null);
  const [locked, setLocked] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const [reviewer, setReviewer] = useState<string | null>(credentials.reviewer);
  const [query, setQuery] = useState("");
  const [toast, setToast] = useState<string | null>(null);
  const [menu, setMenu] = useState(false);
  const search = useRef<HTMLInputElement>(null);
  const timer = useRef<number>();

  const say = useCallback((text: string) => {
    setToast(text);
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setToast(null), 2600);
  }, []);

  const load = useCallback(async () => {
    try {
      setSession(await api.get<Session>("/session"));
      setLocked(false);
      setFailure(null);
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) setLocked(true);
      else setFailure(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    void load();
    const onHash = () => setRoute(fromHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, [load]);

  // "/" focuses search, as the key badge in the search box says.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      if (e.key === "/" && t?.tagName !== "INPUT" && t?.tagName !== "TEXTAREA") {
        e.preventDefault();
        search.current?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const signOut = () => {
    credentials.reviewer = null;
    credentials.token = null;
    setReviewer(null);
    setMenu(false);
  };

  /** A write came back 401/403: the token or the name is wrong, so sign in again. */
  const refused = useCallback(
    (e: unknown): string => {
      if (e instanceof ApiError && (e.status === 401 || e.status === 403) && session?.needs_token) {
        credentials.token = null;
        credentials.reviewer = null;
        setReviewer(null);
      }
      return e instanceof Error ? e.message : String(e);
    },
    [session],
  );

  if (locked) return <PasswordGate onOpen={load} />;
  if (!session)
    return (
      <div className="app">
        <div className="gate">
          {failure ? <p className="error-line">{failure}</p> : <div className="skeleton" style={{ width: 320, height: 18 }} />}
        </div>
      </div>
    );

  const nav = (page: Page, label: string) => (
    <button aria-current={route.page === page ? "page" : undefined} onClick={() => go(page)}>
      {label}
    </button>
  );

  return (
    <div className="app">
      <header className="topbar">
        <div className="wordmark">
          <i aria-hidden="true" />
          Dispute Desk
        </div>
        <nav className="nav" aria-label="Pages">
          {nav("work", "Work")}
          {nav("patterns", "Patterns")}
          {nav("ledger", "Ledger")}
          {nav("team", "Team")}
        </nav>
        <label className="search">
          <span className="sr-only">Search cases</span>
          <input
            ref={search}
            value={query}
            placeholder="Search shipment, seller or status"
            onChange={(e) => {
              setQuery(e.target.value);
              if (route.page !== "work") go("work");
            }}
            onKeyDown={(e) => e.key === "Escape" && setQuery("")}
          />
          <span className="key">/</span>
        </label>
        <span className="spacer" />
        <button className="outline-btn" aria-pressed={route.page === "seller"} onClick={() => go("seller")}>
          Seller view
        </button>
        <span className="evals-link">
          <button className="navlike" aria-current={route.page === "evals" ? "page" : undefined} onClick={() => go("evals")}>
            Evals
          </button>
        </span>
        <div className="role">
          <button onClick={() => setMenu((m) => !m)} aria-expanded={menu}>
            <span className="avatar">{reviewer ? initials(reviewer) : "?"}</span>
            {reviewer ? `${reviewer} · Reviewer` : "Not signed in"}
          </button>
          {menu && (
            <div className="menu" role="menu">
              <p>Your name is recorded against each decision, bound to the exact amount or text.</p>
              {reviewer ? (
                <button role="menuitem" onClick={signOut}>
                  Switch approver
                </button>
              ) : (
                <button role="menuitem" onClick={() => { setMenu(false); go("work"); }}>
                  Sign in to review
                </button>
              )}
              <button role="menuitem" onClick={() => { setMenu(false); go("seller"); }}>
                Seller preview
              </button>
              <button role="menuitem" onClick={() => { setMenu(false); go("ledger"); }}>
                Finance: ledger
              </button>
              <button role="menuitem" onClick={() => { setMenu(false); go("team"); }}>
                Team lead: team
              </button>
              <button role="menuitem" onClick={() => { setMenu(false); go("evals"); }}>
                AI quality: evals
              </button>
            </div>
          )}
        </div>
      </header>
      {session.kill_switch && (
        <div className="killbar" role="alert">
          Kill switch on: the agent takes no action and approvals are paused.
        </div>
      )}
      {route.page === "work" &&
        (reviewer ? (
          <Work
            session={session}
            reviewer={reviewer}
            query={query}
            caseId={route.caseId}
            say={say}
            refused={refused}
          />
        ) : (
          <SignIn session={session} onSignedIn={setReviewer} />
        ))}
      {route.page === "seller" && <SellerPage session={session} say={say} />}
      {route.page === "ledger" && <LedgerPage session={session} />}
      {route.page === "evals" && <EvalsPage />}
      {route.page === "team" && <TeamPage />}
      {route.page === "patterns" && <PatternsPage />}
      {toast && (
        <div className="toast" role="status" aria-live="polite">
          {toast}
        </div>
      )}
    </div>
  );
}

function PasswordGate({ onOpen }: { onOpen: () => Promise<void> }) {
  const [password, setPassword] = useState("");
  return (
    <div className="app">
      <div className="gate">
        <form
          className="sheet pad"
          onSubmit={(e) => {
            e.preventDefault();
            credentials.password = password;
            void onOpen();
          }}
        >
          <h1 style={{ margin: 0, fontSize: 21 }}>Dispute Desk</h1>
          <p className="muted" style={{ margin: 0 }}>
            A live dispute agent on synthetic data. It can send real email to a test address and
            open real Jira issues, so it is behind a password.
          </p>
          <label>
            Site password
            <input type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} />
          </label>
          <button className="btn primary" type="submit">
            Enter
          </button>
        </form>
      </div>
    </div>
  );
}

function SignIn({ session, onSignedIn }: { session: Session; onSignedIn: (name: string) => void }) {
  const [name, setName] = useState(session.approvers[0] ?? "");
  const [token, setToken] = useState("");
  if (session.approvers.length === 0)
    return (
      <div className="gate">
        <p className="error-line">No approver is listed in config/policy.yaml (approvals.approver_ids).</p>
      </div>
    );
  return (
    <div className="gate">
      <form
        className="sheet pad"
        onSubmit={(e) => {
          e.preventDefault();
          credentials.reviewer = name;
          credentials.token = session.needs_token ? token : null;
          onSignedIn(name);
        }}
      >
        <h1 style={{ margin: 0, fontSize: 18 }}>Who is reviewing?</h1>
        <p className="muted" style={{ margin: 0 }}>
          That approver is recorded against each decision. Only approvers listed in the policy
          appear here; the agent ignores decisions by anyone else.
        </p>
        <label>
          Review as
          <select value={name} onChange={(e) => setName(e.target.value)}>
            {session.approvers.map((a) => (
              <option key={a}>{a}</option>
            ))}
          </select>
        </label>
        {session.needs_token && (
          <label>
            Review console token
            <input type="password" value={token} onChange={(e) => setToken(e.target.value)} autoComplete="off" />
          </label>
        )}
        <button className="btn primary" type="submit" disabled={session.needs_token && !token}>
          Start reviewing
        </button>
      </form>
    </div>
  );
}
