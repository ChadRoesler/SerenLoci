import * as vscode from "vscode";
import { SerenConfig, promptSetToken } from "./config";
import { SerenClient } from "./client";
import {
  SetFactTool,
  GetFactTool,
  SearchTool,
  ForgetFactTool,
  HistoryTool,
  ListFactsTool,
} from "./tools";

let statusBar: vscode.StatusBarItem;

export async function activate(context: vscode.ExtensionContext): Promise<void> {
  const config = new SerenConfig(context.secrets);
  const client = new SerenClient(config);

  // -- status bar -------------------------------------------------------------
  statusBar = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 100);
  statusBar.command = "serenLoci.checkHealth";
  statusBar.text = "$(database) Loci";
  statusBar.tooltip = "Seren Loci - click to check service health";
  statusBar.show();
  context.subscriptions.push(statusBar);

  // -- commands ---------------------------------------------------------------
  context.subscriptions.push(
    vscode.commands.registerCommand("serenLoci.setToken", () =>
      promptSetToken(config)
    ),

    vscode.commands.registerCommand("serenLoci.checkHealth", async () => {
      const alive = await client.ping();
      setStatusBar(alive);
      vscode.window.showInformationMessage(
        alive
          ? "Seren Loci: service is reachable ✓"
          : "Seren Loci: service is not reachable ✗"
      );
    }),

    vscode.commands.registerCommand("serenLoci.startService", async () => {
      const cmd = config.startCommand;
      const terminal = vscode.window.createTerminal({
        name: "Seren Loci",
        hideFromUser: false,
      });
      terminal.show();
      terminal.sendText(cmd);
      context.subscriptions.push(terminal);

      // poll for up to 15s then re-check
      await waitForService(client, 15);
      const alive = await client.ping();
      setStatusBar(alive);
      if (alive) {
        vscode.window.showInformationMessage("Seren Loci: service started ✓");
      } else {
        vscode.window.showWarningMessage(
          "Seren Loci: service may still be starting - check the terminal."
        );
      }
    })
  );

  // -- register LM tools (the Loci 6) -----------------------------------------
  context.subscriptions.push(
    vscode.lm.registerTool("seren_loci_set_fact", new SetFactTool(client)),
    vscode.lm.registerTool("seren_loci_get_fact", new GetFactTool(client)),
    vscode.lm.registerTool("seren_loci_search", new SearchTool(client)),
    vscode.lm.registerTool("seren_loci_forget_fact", new ForgetFactTool(client)),
    vscode.lm.registerTool("seren_loci_history", new HistoryTool(client)),
    vscode.lm.registerTool("seren_loci_list_facts", new ListFactsTool(client))
  );

  // -- startup health check ---------------------------------------------------
  const alive = await client.ping();
  setStatusBar(alive);

  if (!alive && !config.suppressStartPrompt) {
    const choice = await vscode.window.showWarningMessage(
      "Seren Loci: service is not reachable. Would you like to start it?",
      "Start Service",
      "Set Endpoint",
      "Don't Ask Again",
      "Dismiss"
    );
    if (choice === "Start Service") {
      vscode.commands.executeCommand("serenLoci.startService");
    } else if (choice === "Set Endpoint") {
      vscode.commands.executeCommand(
        "workbench.action.openSettings",
        "serenLoci.endpoint"
      );
    } else if (choice === "Don't Ask Again") {
      await config.setSuppressStartPrompt(true);
      vscode.window.showInformationMessage(
        "Seren Loci: startup prompt suppressed. " +
        "Toggle 'serenLoci.suppressStartPrompt' in settings to re-enable."
      );
    }
  }
}

export function deactivate(): void {
  statusBar?.dispose();
}

// -- helpers ----------------------------------------------------------------

function setStatusBar(alive: boolean): void {
  if (alive) {
    statusBar.text = "$(database) Loci ✓";
    statusBar.backgroundColor = undefined;
    statusBar.tooltip = "Seren Loci - service reachable";
  } else {
    statusBar.text = "$(database) Loci ✗";
    statusBar.backgroundColor = new vscode.ThemeColor(
      "statusBarItem.warningBackground"
    );
    statusBar.tooltip = "Seren Loci - service not reachable. Click to check again.";
  }
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function waitForService(client: SerenClient, maxSeconds: number): Promise<void> {
  const deadline = Date.now() + maxSeconds * 1000;
  while (Date.now() < deadline) {
    await sleep(1000);
    if (await client.ping()) {
      return;
    }
  }
}
