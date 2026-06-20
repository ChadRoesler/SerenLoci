import * as vscode from "vscode";

const SECRET_KEY = "serenLoci.bearerToken";

export class SerenConfig {
  constructor(private readonly secrets: vscode.SecretStorage) {}

  get endpoint(): string {
    const raw = vscode.workspace
      .getConfiguration("serenLoci")
      .get<string>("endpoint", "http://localhost:7422");
    return raw.replace(/\/$/, "");
  }

  get startCommand(): string {
    return vscode.workspace
      .getConfiguration("serenLoci")
      .get<string>("startCommand", "python -m seren_loci");
  }

  /** When true, suppress the "service not reachable - start it?" prompt
   *  on extension activation. User-toggleable via the don't-ask-again
   *  button on that prompt, or directly in settings. */
  get suppressStartPrompt(): boolean {
    return vscode.workspace
      .getConfiguration("serenLoci")
      .get<boolean>("suppressStartPrompt", false);
  }

  async setSuppressStartPrompt(value: boolean): Promise<void> {
    await vscode.workspace
      .getConfiguration("serenLoci")
      .update("suppressStartPrompt", value, vscode.ConfigurationTarget.Global);
  }

  async getToken(): Promise<string | undefined> {
    return this.secrets.get(SECRET_KEY);
  }

  async setToken(token: string): Promise<void> {
    await this.secrets.store(SECRET_KEY, token);
  }

  async deleteToken(): Promise<void> {
    await this.secrets.delete(SECRET_KEY);
  }

  async getHeaders(): Promise<Record<string, string>> {
    const headers: Record<string, string> = {
      "Content-Type": "application/json",
    };
    const token = await this.getToken();
    if (token) {
      headers["Authorization"] = `Bearer ${token}`;
    }
    return headers;
  }
}

/** Prompt the user to enter and persist a bearer token. */
export async function promptSetToken(config: SerenConfig): Promise<void> {
  const token = await vscode.window.showInputBox({
    title: "Seren Loci: Set Bearer Token",
    prompt: "Enter the bearer token for your SerenLoci service (leave blank to clear).",
    password: true,
    ignoreFocusOut: true,
  });
  if (token === undefined) {
    return; // cancelled
  }
  if (token === "") {
    await config.deleteToken();
    vscode.window.showInformationMessage("Seren Loci: bearer token cleared.");
  } else {
    await config.setToken(token);
    vscode.window.showInformationMessage("Seren Loci: bearer token saved.");
  }
}
