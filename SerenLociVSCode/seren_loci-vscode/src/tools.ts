import * as vscode from "vscode";
import { SerenClient, SerenApiError } from "./client";

// -- helpers ----------------------------------------------------------------

function ok(text: string): vscode.LanguageModelToolResult {
  return new vscode.LanguageModelToolResult([
    new vscode.LanguageModelTextPart(text),
  ]);
}

function err(e: unknown): vscode.LanguageModelToolResult {
  if (e instanceof SerenApiError) {
    return ok(`Error ${e.status}: ${JSON.stringify(e.body)}`);
  }
  if (e instanceof Error && e.name === "AbortError") {
    return ok("Cancelled by user/host.");
  }
  return ok(`Error: ${String(e)}`);
}

function json(data: unknown): vscode.LanguageModelToolResult {
  return ok(JSON.stringify(data, null, 2));
}

/** Bridge VS Code's CancellationToken to an AbortSignal so the underlying
 *  fetch can actually cancel. Without this, a hung SerenLoci means the tool
 *  call hangs forever regardless of VS Code's cancel button. */
function signalFromToken(token: vscode.CancellationToken): AbortSignal {
  const controller = new AbortController();
  if (token.isCancellationRequested) {
    controller.abort();
  } else {
    token.onCancellationRequested(() => controller.abort());
  }
  return controller.signal;
}

// Loci's reserved fundamentals scope - cross-project truths. Mirrors
// seren_loci.models.schemas.FUNDAMENTALS.
const FUNDAMENTALS = "*";

// -- seren_loci_set_fact ----------------------------------------------------

interface SetFactInput {
  key: string;
  value: string;
  why?: string;
  project?: string;
}

export class SetFactTool implements vscode.LanguageModelTool<SetFactInput> {
  constructor(private readonly client: SerenClient) {}

  async invoke(
    options: vscode.LanguageModelToolInvocationOptions<SetFactInput>,
    token: vscode.CancellationToken
  ): Promise<vscode.LanguageModelToolResult> {
    const { key, value, why, project = FUNDAMENTALS } = options.input;
    try {
      const result = await this.client.setFact(
        key, value, why, project, signalFromToken(token));
      return json(result);
    } catch (e) {
      return err(e);
    }
  }
}

// -- seren_loci_get_fact ----------------------------------------------------

interface GetFactInput {
  key: string;
  project?: string;
}

export class GetFactTool implements vscode.LanguageModelTool<GetFactInput> {
  constructor(private readonly client: SerenClient) {}

  async invoke(
    options: vscode.LanguageModelToolInvocationOptions<GetFactInput>,
    token: vscode.CancellationToken
  ): Promise<vscode.LanguageModelToolResult> {
    const { key, project = FUNDAMENTALS } = options.input;
    try {
      const result = await this.client.getFact(key, project, signalFromToken(token));
      return json(result);
    } catch (e) {
      // A 404 means "no live value for this key" - a clean answer, not an
      // error. Surface it as a found:false result so the model reads it as
      // 'nothing set' rather than a failure.
      if (e instanceof SerenApiError && e.status === 404) {
        return json({ found: false, project, key });
      }
      return err(e);
    }
  }
}

// -- seren_loci_search ------------------------------------------------------

interface SearchInput {
  query: string;
  n_results?: number;
  project?: string;
  include_fundamentals?: boolean;
  include_superseded?: boolean;
}

export class SearchTool implements vscode.LanguageModelTool<SearchInput> {
  constructor(private readonly client: SerenClient) {}

  async invoke(
    options: vscode.LanguageModelToolInvocationOptions<SearchInput>,
    token: vscode.CancellationToken
  ): Promise<vscode.LanguageModelToolResult> {
    const {
      query,
      n_results = 10,
      project,
      include_fundamentals = true,
      include_superseded = false,
    } = options.input;
    try {
      const result = await this.client.search(
        query, n_results, project, include_fundamentals, include_superseded,
        signalFromToken(token)
      );
      return json(result);
    } catch (e) {
      return err(e);
    }
  }
}

// -- seren_loci_forget_fact -------------------------------------------------

interface ForgetFactInput {
  key: string;
  project?: string;
}

export class ForgetFactTool implements vscode.LanguageModelTool<ForgetFactInput> {
  constructor(private readonly client: SerenClient) {}

  async invoke(
    options: vscode.LanguageModelToolInvocationOptions<ForgetFactInput>,
    token: vscode.CancellationToken
  ): Promise<vscode.LanguageModelToolResult> {
    const { key, project = FUNDAMENTALS } = options.input;
    try {
      const result = await this.client.forgetFact(key, project, signalFromToken(token));
      return json(result);
    } catch (e) {
      // 404 == nothing live to retire; a clean answer.
      if (e instanceof SerenApiError && e.status === 404) {
        return json({ ok: false, project, key, note: "no live value to retire" });
      }
      return err(e);
    }
  }
}

// -- seren_loci_history -----------------------------------------------------

interface HistoryInput {
  key: string;
  project?: string;
}

export class HistoryTool implements vscode.LanguageModelTool<HistoryInput> {
  constructor(private readonly client: SerenClient) {}

  async invoke(
    options: vscode.LanguageModelToolInvocationOptions<HistoryInput>,
    token: vscode.CancellationToken
  ): Promise<vscode.LanguageModelToolResult> {
    const { key, project = FUNDAMENTALS } = options.input;
    try {
      const result = await this.client.factHistory(key, project, signalFromToken(token));
      return json(result);
    } catch (e) {
      return err(e);
    }
  }
}

// -- seren_loci_list_facts --------------------------------------------------

interface ListFactsInput {
  project?: string;
  include_superseded?: boolean;
}

export class ListFactsTool implements vscode.LanguageModelTool<ListFactsInput> {
  constructor(private readonly client: SerenClient) {}

  async invoke(
    options: vscode.LanguageModelToolInvocationOptions<ListFactsInput>,
    token: vscode.CancellationToken
  ): Promise<vscode.LanguageModelToolResult> {
    const { project, include_superseded = false } = options.input;
    try {
      const result = await this.client.listFacts(
        project, include_superseded, signalFromToken(token));
      return json(result);
    } catch (e) {
      return err(e);
    }
  }
}
