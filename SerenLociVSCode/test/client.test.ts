/**
 * Unit tests for SerenClient (the left brain's HTTP client).
 *
 * Uses vitest + fetch mocking (no VS Code host, no live service).
 * The `vscode` module is aliased to test/mocks/vscode.ts in vitest.config.ts
 * so the import chain (client -> config -> vscode) resolves cleanly.
 *
 * Pattern: for each method, stub globalThis.fetch to return a canned response,
 * call the method, assert the right URL/method/body was sent and the return
 * value is passed through.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { SerenClient, SerenApiError } from "../seren_loci-vscode/src/client";
import { SerenConfig } from "../seren_loci-vscode/src/config";
import { SecretStorage } from "./mocks/vscode";

// -- helpers ------------------------------------------------------------------

function makeClient(endpoint = "http://localhost:7422"): SerenClient {
  const secrets = new SecretStorage();
  const config = new SerenConfig(secrets as any);
  // SerenConfig reads endpoint from the vscode stub (which returns defaults);
  // pin it to a known value so URL assertions are stable.
  Object.defineProperty(config, "endpoint", { get: () => endpoint });
  return new SerenClient(config);
}

function mockFetch(status: number, body: unknown): void {
  const response = new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response));
}

function lastFetch() {
  return (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0] as [string, RequestInit];
}

beforeEach(() => vi.restoreAllMocks());
afterEach(() => vi.restoreAllMocks());

// -- ping ---------------------------------------------------------------------

describe("ping", () => {
  it("returns true when /health responds", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("{}", { status: 200 })));
    expect(await makeClient().ping()).toBe(true);
  });

  it("returns false when fetch throws (service down)", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("ECONNREFUSED")));
    expect(await makeClient().ping()).toBe(false);
  });
});

// -- setFact ------------------------------------------------------------------

describe("setFact", () => {
  it("POSTs to /fact with project, key, value (why omitted)", async () => {
    mockFetch(200, { ok: true, superseded: null });
    const result = await makeClient().setFact("posh.brace_style", "new line", undefined, "*");
    const [url, init] = lastFetch();
    expect(url).toBe("http://localhost:7422/fact");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual({
      project: "*",
      key: "posh.brace_style",
      value: "new line",
    });
    expect(result).toEqual({ ok: true, superseded: null });
  });

  it("includes why when provided and uses a concrete project", async () => {
    mockFetch(200, { ok: true });
    await makeClient().setFact("cuda.no_vmm", "ON at compile time", "env var not honored at runtime", "seren-memory");
    const [, init] = lastFetch();
    expect(JSON.parse(init.body as string)).toEqual({
      project: "seren-memory",
      key: "cuda.no_vmm",
      value: "ON at compile time",
      why: "env var not honored at runtime",
    });
  });

  it("defaults project to fundamentals (*)", async () => {
    mockFetch(200, { ok: true });
    await makeClient().setFact("k", "v");
    const [, init] = lastFetch();
    expect(JSON.parse(init.body as string).project).toBe("*");
  });
});

// -- getFact ------------------------------------------------------------------

describe("getFact", () => {
  it("GETs /fact with project + key query", async () => {
    mockFetch(200, { project: "*", key: "k", value: "v" });
    await makeClient().getFact("posh.brace_style");
    const [url, init] = lastFetch();
    expect(url).toBe("http://localhost:7422/fact?project=*&key=posh.brace_style");
    expect(init.method).toBe("GET");
    expect(init.body).toBeUndefined();
  });
});

// -- factHistory --------------------------------------------------------------

describe("factHistory", () => {
  it("GETs /fact/history with project + key", async () => {
    mockFetch(200, { history: [], count: 0 });
    await makeClient().factHistory("k", "seren-memory");
    const [url, init] = lastFetch();
    expect(url).toBe("http://localhost:7422/fact/history?project=seren-memory&key=k");
    expect(init.method).toBe("GET");
  });
});

// -- forgetFact ---------------------------------------------------------------

describe("forgetFact", () => {
  it("DELETEs /fact with project + key", async () => {
    mockFetch(200, { ok: true });
    await makeClient().forgetFact("k");
    const [url, init] = lastFetch();
    expect(url).toBe("http://localhost:7422/fact?project=*&key=k");
    expect(init.method).toBe("DELETE");
    expect(init.body).toBeUndefined();
  });
});

// -- listFacts ----------------------------------------------------------------

describe("listFacts", () => {
  it("GETs /facts with no query when no args", async () => {
    mockFetch(200, { facts: [], count: 0 });
    await makeClient().listFacts();
    const [url, init] = lastFetch();
    expect(url).toBe("http://localhost:7422/facts");
    expect(init.method).toBe("GET");
  });

  it("adds project and include_superseded when set", async () => {
    mockFetch(200, { facts: [], count: 0 });
    await makeClient().listFacts("seren-memory", true);
    const [url] = lastFetch();
    expect(url).toBe("http://localhost:7422/facts?project=seren-memory&include_superseded=true");
  });
});

// -- search -------------------------------------------------------------------

describe("search", () => {
  it("POSTs to /search with defaults (project omitted)", async () => {
    mockFetch(200, { hits: [], finder: "lexical" });
    await makeClient().search("cuda runtime", 5);
    const [url, init] = lastFetch();
    expect(url).toBe("http://localhost:7422/search");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual({
      query: "cuda runtime",
      n_results: 5,
      include_fundamentals: true,
      include_superseded: false,
    });
  });

  it("includes project when provided", async () => {
    mockFetch(200, { hits: [] });
    await makeClient().search("brace", 10, "seren-memory", false, true);
    const [, init] = lastFetch();
    expect(JSON.parse(init.body as string)).toEqual({
      query: "brace",
      n_results: 10,
      include_fundamentals: false,
      include_superseded: true,
      project: "seren-memory",
    });
  });
});

// -- SerenApiError ------------------------------------------------------------

describe("SerenApiError", () => {
  it("is thrown on non-2xx responses", async () => {
    mockFetch(404, { detail: "no live fact" });
    await expect(makeClient().getFact("nope")).rejects.toBeInstanceOf(SerenApiError);
  });

  it("carries status and body", async () => {
    mockFetch(404, { detail: "no live fact" });
    try {
      await makeClient().getFact("nope");
    } catch (e) {
      expect(e).toBeInstanceOf(SerenApiError);
      expect((e as SerenApiError).status).toBe(404);
      expect((e as SerenApiError).body).toEqual({ detail: "no live fact" });
    }
  });
});

// -- URL encoding -------------------------------------------------------------

describe("URL encoding", () => {
  it("encodes special characters in keys (now query params, not path)", async () => {
    mockFetch(200, { value: "v" });
    await makeClient().getFact("weird/key with spaces", "proj a");
    const [url] = lastFetch();
    expect(url).toBe(
      "http://localhost:7422/fact?project=proj%20a&key=weird%2Fkey%20with%20spaces"
    );
  });
});
