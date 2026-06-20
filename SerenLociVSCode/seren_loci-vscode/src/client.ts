import { SerenConfig } from "./config";

export class SerenApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly body: unknown,
    message: string
  ) {
    super(message);
    this.name = "SerenApiError";
  }
}

/**
 * HTTP client for SerenLoci - the left brain. Every method's payload matches
 * the actual route contract on the SerenLoci side, verified against the live
 * pydantic schemas. Don't drift; the backend uses pydantic v2 with the default
 * extra="ignore", so an unknown field is SILENTLY DROPPED - a typo'd field
 * name doesn't 400, it just vanishes and the call appears to "succeed" with
 * default behaviour.
 *
 * Every request takes an optional AbortSignal so the VS Code cancellation
 * token from a tool's invoke() can actually cancel the in-flight fetch.
 */
export class SerenClient {
  constructor(private readonly config: SerenConfig) {}

  // -- helpers ----------------------------------------------------------------

  private async request<T>(
    method: string,
    path: string,
    body?: unknown,
    signal?: AbortSignal
  ): Promise<T> {
    const headers = await this.config.getHeaders();
    const response = await fetch(`${this.config.endpoint}${path}`, {
      method,
      headers,
      body: body !== undefined ? JSON.stringify(body) : undefined,
      signal,
    });

    let json: unknown;
    const ct = response.headers.get("content-type") ?? "";
    if (ct.includes("application/json")) {
      json = await response.json();
    } else {
      json = await response.text();
    }

    if (!response.ok) {
      throw new SerenApiError(
        response.status,
        json,
        `SerenLoci ${method} ${path} failed: ${response.status}`
      );
    }
    return json as T;
  }

  private get<T>(path: string, signal?: AbortSignal): Promise<T> {
    return this.request<T>("GET", path, undefined, signal);
  }

  private post<T>(path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
    return this.request<T>("POST", path, body, signal);
  }

  private del<T>(path: string, signal?: AbortSignal): Promise<T> {
    return this.request<T>("DELETE", path, undefined, signal);
  }

  /** Build a ?project=&key= query string. project/key are free-form strings
   *  (a key like 'posh.brace_style', a project of '*'), so always encode. */
  private factQuery(key: string, project: string): string {
    return `?project=${encodeURIComponent(project)}&key=${encodeURIComponent(key)}`;
  }

  // -- health -----------------------------------------------------------------

  async ping(): Promise<boolean> {
    try {
      await fetch(`${this.config.endpoint}/health`, { signal: AbortSignal.timeout(3000) });
      return true;
    } catch {
      return false;
    }
  }

  // -- facts ------------------------------------------------------------------
  //
  // CONTRACT NOTES (don't drift):
  //   POST   /fact          FactWrite -> { project, key, value, why? }
  //                         project omitted defaults to '*' (fundamentals).
  //                         Returns { ok, fact, superseded: id|null }.
  //   GET    /fact          ?project=&key=  -> the live Fact, or 404 if none.
  //   GET    /fact/history  ?project=&key=  -> { history: Fact[], count }.
  //   DELETE /fact          ?project=&key=  -> soft-retire the live value (404
  //                         if nothing live to retire).
  //   GET    /facts         ?project=&include_superseded=  -> { facts, count }.
  //   POST   /search        SearchRequest -> {
  //     query, n_results (NOT `limit`), project? (null = all scopes),
  //     include_fundamentals, include_superseded
  //   }
  //   NOTE: there is no /short, /near, /long, /brief, /consolidate, /drafts -
  //   that's the right brain (SerenMemory). Loci is deterministic facts.

  async setFact(
    key: string,
    value: string,
    why?: string,
    project: string = "*",
    signal?: AbortSignal
  ): Promise<unknown> {
    const body: Record<string, unknown> = { project, key, value };
    if (why !== undefined && why !== "") body.why = why;
    return this.post("/fact", body, signal);
  }

  async getFact(key: string, project: string = "*", signal?: AbortSignal): Promise<unknown> {
    return this.get(`/fact${this.factQuery(key, project)}`, signal);
  }

  async factHistory(key: string, project: string = "*", signal?: AbortSignal): Promise<unknown> {
    return this.get(`/fact/history${this.factQuery(key, project)}`, signal);
  }

  async forgetFact(key: string, project: string = "*", signal?: AbortSignal): Promise<unknown> {
    return this.del(`/fact${this.factQuery(key, project)}`, signal);
  }

  async listFacts(
    project?: string,
    include_superseded: boolean = false,
    signal?: AbortSignal
  ): Promise<unknown> {
    const params = new URLSearchParams();
    if (project !== undefined && project !== null) params.set("project", project);
    if (include_superseded) params.set("include_superseded", "true");
    const qs = params.toString() ? `?${params.toString()}` : "";
    return this.get(`/facts${qs}`, signal);
  }

  async search(
    query: string,
    n_results: number = 10,
    project?: string,
    include_fundamentals: boolean = true,
    include_superseded: boolean = false,
    signal?: AbortSignal
  ): Promise<unknown> {
    const body: Record<string, unknown> = {
      query,
      n_results,
      include_fundamentals,
      include_superseded,
    };
    if (project !== undefined && project !== null) body.project = project;
    return this.post("/search", body, signal);
  }
}
