#!/usr/bin/env node

import { randomUUID } from "node:crypto";
import { appendFileSync, existsSync, mkdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { pathToFileURL } from "node:url";

import { refreshDashboard } from "./refresh-dashboard.mjs";

function argumentValue(args, name) {
  const prefix = `--${name}=`;
  return args.find((argument) => argument.startsWith(prefix))?.slice(prefix.length);
}

export function recordEvent(rootPath, options) {
  const root = resolve(rootPath);
  if (!options.type || !/^[a-z][a-z0-9_.-]+$/.test(options.type)) {
    throw new Error("event type is required and must use lowercase dot notation");
  }
  if (!options.message?.trim()) throw new Error("event message is required");

  const eventPath = resolve(root, ".automation/events.jsonl");
  mkdirSync(dirname(eventPath), { recursive: true });
  if (!existsSync(eventPath)) appendFileSync(eventPath, "", "utf8");
  const event = {
    schema: "semiclass-automation-event/v1",
    event_id: `evt-${randomUUID()}`,
    event_type: options.type,
    occurred_at: new Date().toISOString(),
    actor: options.actor ?? "agent",
    run_id: options.runId ?? null,
    message: options.message.trim(),
    data: options.data ?? {},
  };
  appendFileSync(eventPath, `${JSON.stringify(event)}\n`, "utf8");
  const refreshed = refreshDashboard(root);
  return { event, dashboard_path: refreshed.dashboard_path };
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const args = process.argv.slice(2);
  const rootPath = args.find((argument) => !argument.startsWith("--")) ?? ".";
  const dataJson = argumentValue(args, "data-json");
  try {
    const result = recordEvent(rootPath, {
      type: argumentValue(args, "type"),
      message: argumentValue(args, "message"),
      actor: argumentValue(args, "actor"),
      runId: argumentValue(args, "run-id"),
      data: dataJson ? JSON.parse(dataJson) : {},
    });
    console.log(JSON.stringify(result));
  } catch (error) {
    console.error(error instanceof Error ? error.message : String(error));
    process.exit(1);
  }
}
