// The one way the web app talks to the dispute service (/api/ws). Credentials live in this
// tab's sessionStorage only: closing the tab signs out.

const BASE = `${import.meta.env.BASE_URL.replace(/\/app\/$/, "/")}api/ws`;

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

const KEYS = { password: "dd-password", token: "dd-token", reviewer: "dd-reviewer" } as const;

function read(key: string): string | null {
  try {
    return sessionStorage.getItem(key);
  } catch {
    return null;
  }
}

function write(key: string, value: string | null): void {
  try {
    if (value === null) sessionStorage.removeItem(key);
    else sessionStorage.setItem(key, value);
  } catch {
    /* private mode: the session lasts as long as the page */
  }
}

export const credentials = {
  get password() {
    return read(KEYS.password);
  },
  set password(v: string | null) {
    write(KEYS.password, v);
  },
  get token() {
    return read(KEYS.token);
  },
  set token(v: string | null) {
    write(KEYS.token, v);
  },
  get reviewer() {
    return read(KEYS.reviewer);
  },
  set reviewer(v: string | null) {
    write(KEYS.reviewer, v);
  },
};

function headers(json: boolean): HeadersInit {
  const h: Record<string, string> = { Accept: "application/json" };
  if (json) h["Content-Type"] = "application/json";
  if (credentials.password) h["X-Console-Password"] = credentials.password;
  if (credentials.token) h["X-Review-Token"] = credentials.token;
  return h;
}

/** A reply the server meant: its own detail, or a plain sentence. Never a proxy's HTML page. */
export function describeFailure(status: number, body: unknown): string {
  if (body && typeof body === "object" && "detail" in body) {
    const detail = (body as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) return "Some of what was sent was not accepted.";
  }
  if (status === 0) return "The dispute service is not reachable. Is it running?";
  if (status >= 500) return "The dispute service had a problem. Try again in a moment.";
  return `The request was refused (HTTP ${status}).`;
}

async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
  let answer: Response;
  try {
    answer = await fetch(`${BASE}${path}`, {
      method,
      headers: headers(body !== undefined),
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError(0, describeFailure(0, null));
  }
  const type = answer.headers.get("content-type") ?? "";
  const parsed: unknown = type.includes("application/json") ? await answer.json() : null;
  if (!answer.ok) throw new ApiError(answer.status, describeFailure(answer.status, parsed));
  if (parsed === null) throw new ApiError(answer.status, describeFailure(502, null));
  return parsed as T;
}

export const api = {
  get: <T>(path: string) => call<T>("GET", path),
  post: <T>(path: string, body: unknown) => call<T>("POST", path, body),
  photo: (ref: string) => `${BASE}/photos/${encodeURIComponent(ref)}`,
};
