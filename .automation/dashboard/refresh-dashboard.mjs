#!/usr/bin/env node

import { createHash } from "node:crypto";
import { existsSync, readFileSync, readdirSync, statSync, writeFileSync } from "node:fs";
import { basename, dirname, relative, resolve, sep } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const SCRIPT_DIRECTORY = dirname(fileURLToPath(import.meta.url));
const DASHBOARD_PLACEHOLDER = "__SEMICLASS_DASHBOARD_DATA__";
const SKIPPED_NAMES = new Set([".DS_Store", ".keep", ".gitkeep", "README.md"]);

function normalizePath(value) {
  return value.split(sep).join("/").normalize("NFC");
}

function splitKeyValue(value) {
  let quote = null;
  let bracketDepth = 0;
  for (let index = 0; index < value.length; index += 1) {
    const character = value[index];
    if (quote) {
      if (character === quote && value[index - 1] !== "\\") quote = null;
      continue;
    }
    if (character === '"' || character === "'") {
      quote = character;
      continue;
    }
    if (character === "[" || character === "{") bracketDepth += 1;
    if (character === "]" || character === "}") bracketDepth -= 1;
    if (character === ":" && bracketDepth === 0) {
      return [value.slice(0, index).trim(), value.slice(index + 1).trim()];
    }
  }
  return [value.trim(), null];
}

function splitInlineList(value) {
  const items = [];
  let quote = null;
  let start = 0;
  for (let index = 0; index < value.length; index += 1) {
    const character = value[index];
    if (quote) {
      if (character === quote && value[index - 1] !== "\\") quote = null;
      continue;
    }
    if (character === '"' || character === "'") quote = character;
    if (character === "," && !quote) {
      items.push(value.slice(start, index).trim());
      start = index + 1;
    }
  }
  items.push(value.slice(start).trim());
  return items.filter(Boolean);
}

function parseScalar(value) {
  const trimmed = value.trim();
  if (trimmed === "") return "";
  if (trimmed === "[]") return [];
  if (trimmed === "{}") return {};
  if (trimmed.startsWith("[") && trimmed.endsWith("]")) {
    return splitInlineList(trimmed.slice(1, -1)).map(parseScalar);
  }
  if (
    (trimmed.startsWith('"') && trimmed.endsWith('"')) ||
    (trimmed.startsWith("'") && trimmed.endsWith("'"))
  ) {
    return trimmed.slice(1, -1);
  }
  if (trimmed === "true") return true;
  if (trimmed === "false") return false;
  if (trimmed === "null" || trimmed === "~") return null;
  if (/^-?\d+(\.\d+)?$/.test(trimmed)) return Number(trimmed);
  return trimmed;
}

export function parseSimpleYaml(source) {
  const lines = source
    .split(/\r?\n/)
    .map((raw) => ({
      indent: raw.match(/^ */)?.[0].length ?? 0,
      content: raw.trim(),
    }))
    .filter((line) => line.content && !line.content.startsWith("#"));

  function parseBlock(startIndex, indent) {
    const isList = lines[startIndex]?.content.startsWith("- ") ?? false;
    const container = isList ? [] : {};
    let index = startIndex;

    while (index < lines.length) {
      const line = lines[index];
      if (line.indent < indent) break;
      if (line.indent > indent) {
        index += 1;
        continue;
      }

      if (isList) {
        if (!line.content.startsWith("- ")) break;
        const itemSource = line.content.slice(2).trim();
        const [key, rest] = splitKeyValue(itemSource);
        if (rest !== null) {
          const item = {};
          if (rest !== "") {
            item[key] = parseScalar(rest);
            index += 1;
          } else if (lines[index + 1]?.indent > indent) {
            const nested = parseBlock(index + 1, lines[index + 1].indent);
            item[key] = nested.value;
            index = nested.index;
          } else {
            item[key] = {};
            index += 1;
          }

          if (lines[index]?.indent > indent) {
            const continuation = parseBlock(index, lines[index].indent);
            if (
              continuation.value &&
              typeof continuation.value === "object" &&
              !Array.isArray(continuation.value)
            ) {
              Object.assign(item, continuation.value);
            }
            index = continuation.index;
          }
          container.push(item);
        } else {
          container.push(parseScalar(itemSource));
          index += 1;
        }
        continue;
      }

      if (line.content.startsWith("- ")) break;
      const [key, rest] = splitKeyValue(line.content);
      if (rest === null) {
        index += 1;
        continue;
      }
      if (rest !== "") {
        container[key] = parseScalar(rest);
        index += 1;
      } else if (lines[index + 1]?.indent > indent) {
        const nested = parseBlock(index + 1, lines[index + 1].indent);
        container[key] = nested.value;
        index = nested.index;
      } else {
        container[key] = {};
        index += 1;
      }
    }

    return { value: container, index };
  }

  if (lines.length === 0) return {};
  return parseBlock(0, lines[0].indent).value;
}

function readYaml(path) {
  if (!existsSync(path)) return {};
  return parseSimpleYaml(readFileSync(path, "utf8"));
}

function readJson(path, fallback = null) {
  if (!existsSync(path)) return fallback;
  try {
    return JSON.parse(readFileSync(path, "utf8"));
  } catch {
    return fallback;
  }
}

function listFiles(root, relativeDirectory) {
  const directory = resolve(root, relativeDirectory);
  if (!existsSync(directory)) return [];
  const files = [];

  function walk(current) {
    for (const entry of readdirSync(current, { withFileTypes: true })) {
      if (SKIPPED_NAMES.has(entry.name)) continue;
      const path = resolve(current, entry.name);
      if (entry.isDirectory()) walk(path);
      if (entry.isFile()) {
        const stat = statSync(path);
        files.push({
          path: normalizePath(relative(root, path)),
          name: entry.name.normalize("NFC"),
          size: stat.size,
          updated_at: stat.mtime.toISOString(),
          sha256: createHash("sha256").update(readFileSync(path)).digest("hex"),
        });
      }
    }
  }

  walk(directory);
  return files.sort((left, right) => left.path.localeCompare(right.path));
}

function readEvents(root) {
  const path = resolve(root, ".automation/events.jsonl");
  if (!existsSync(path)) return [];
  return readFileSync(path, "utf8")
    .split(/\r?\n/)
    .filter(Boolean)
    .map((line) => {
      try {
        return JSON.parse(line);
      } catch {
        return null;
      }
    })
    .filter(Boolean)
    .sort(
      (left, right) =>
        (Date.parse(right.occurred_at ?? "") || 0) - (Date.parse(left.occurred_at ?? "") || 0),
    );
}

function readRuns(root) {
  const directory = resolve(root, ".automation/runs");
  if (!existsSync(directory)) return [];
  return readdirSync(directory, { withFileTypes: true })
    .filter((entry) => entry.isDirectory())
    .map((entry) => readJson(resolve(directory, entry.name, "manifest.json")))
    .filter(Boolean)
    .sort(
      (left, right) =>
        (Date.parse(right.started_at ?? "") || 0) - (Date.parse(left.started_at ?? "") || 0),
    );
}

function asArray(value) {
  if (Array.isArray(value)) return value;
  if (value === undefined || value === null || value === "") return [];
  return [value];
}

function stageLabel(stages, id) {
  return stages.find((stage) => stage.id === id)?.name ?? id ?? "확인 필요";
}

function roleFiles(root) {
  return {
    input: listFiles(root, "input"),
    input_form: listFiles(root, "reference/forms/input"),
    output_form: listFiles(root, "reference/forms/output"),
    policy: listFiles(root, "reference/policies"),
    example: listFiles(root, "reference/examples"),
    output: listFiles(root, "output"),
  };
}

export function buildDashboardModel(rootPath) {
  const root = resolve(rootPath);
  const automationDocument = readYaml(resolve(root, "design/automation.yaml"));
  const roadmapDocument = readYaml(resolve(root, "design/roadmap.yaml"));
  const automation = automationDocument.automation ?? {};
  const stages = asArray(roadmapDocument.stages);
  const filesByRole = roleFiles(root);
  const events = readEvents(root);
  const runs = readRuns(root);
  const previousDashboard = readJson(resolve(root, ".automation/dashboard.json"), {});
  const warnings = new Set(asArray(previousDashboard?.warnings));

  if (filesByRole.input.length === 0) warnings.add("no_actual_input");
  else warnings.delete("no_actual_input");
  if (existsSync(resolve(root, ".automation/archive"))) {
    warnings.delete("legacy_structure_detected");
    warnings.add("legacy_archive_available");
  }

  const completedStages = stages.filter((stage) => stage.status === "done").length;
  const process = asArray(automation.process).map((step, index) => ({
    id: step.id ?? `step-${String(index + 1).padStart(2, "0")}`,
    name: step.name ?? `처리 ${index + 1}`,
    description: step.description ?? "확인 필요",
    human_gate: step.human_gate === true,
  }));
  const outputForms = filesByRole.output_form.map((file) => file.path);
  const inputForms = filesByRole.input_form.map((file) => file.path);

  return {
    schema: "semiclass-dashboard/v2",
    updated_at: new Date().toISOString(),
    automation: {
      id: automation.id ?? "unconfigured",
      name: automation.name ?? "내 업무자동화",
      purpose: automation.purpose ?? "자동화 목적 확인 필요",
      trigger: automation.trigger?.description ?? automation.trigger?.type ?? "사람이 직접 시작",
    },
    flow: {
      trigger: automation.trigger?.description ?? "사람이 직접 시작",
      inputs: filesByRole.input,
      input_description: automation.input?.description ?? "실제 입력 확인 필요",
      input_formats: asArray(automation.input?.formats),
      input_forms: inputForms,
      process,
      outputs: filesByRole.output,
      output_description: automation.output?.description ?? "결과물 확인 필요",
      output_formats: asArray(automation.output?.formats),
      output_path: automation.output?.path_pattern ?? "output/<run-id>/",
      output_forms: outputForms,
      human_review: automation.human_review ?? {
        required: true,
        point: "결과 사용 전",
      },
    },
    roadmap: {
      current_stage: roadmapDocument.current_stage ?? "design",
      current_stage_name: stageLabel(stages, roadmapDocument.current_stage),
      completed: completedStages,
      total: stages.length,
      percent: stages.length === 0 ? 0 : Math.round((completedStages / stages.length) * 100),
      stages,
      next_action: roadmapDocument.next_action?.title ?? "확인 필요",
      completion_evidence: roadmapDocument.next_action?.completion_evidence ?? "확인 필요",
    },
    files: Object.fromEntries(
      Object.entries(filesByRole).map(([role, files]) => [role, files.length]),
    ),
    file_details: filesByRole,
    latest_run: runs[0] ?? null,
    recent_runs: runs.slice(0, 5),
    recent_events: events.slice(0, 12),
    tools: asArray(automation.tools),
    schedules: asArray(automation.schedules),
    runtime: {
      location: automation.runtime?.location ?? "사용자 PC",
      service: automation.runtime?.service ?? "Claude Code 또는 Codex",
      mode: automation.runtime?.mode ?? automation.trigger?.type ?? "manual",
    },
    warnings: [...warnings],
  };
}

function fileIndexFromModel(model) {
  const roleEntries = Object.entries(model.file_details).flatMap(([role, files]) =>
    files.map((file) => ({
      ...file,
      role,
      status: "approved",
      source: "student",
      indexed_at: model.updated_at,
    })),
  );
  return {
    schema: "semiclass-file-index/v1",
    updated_at: model.updated_at,
    files: roleEntries,
  };
}

function resolveTemplate(root) {
  const installed = resolve(root, ".automation/dashboard/dashboard-template.html");
  if (existsSync(installed)) return installed;
  const bundled = resolve(SCRIPT_DIRECTORY, "../assets/dashboard-template.html");
  if (existsSync(bundled)) return bundled;
  throw new Error("dashboard template not found; run install-dashboard.mjs first");
}

export function refreshDashboard(rootPath) {
  const root = resolve(rootPath);
  const model = buildDashboardModel(root);
  const template = readFileSync(resolveTemplate(root), "utf8");
  if (!template.includes(DASHBOARD_PLACEHOLDER)) {
    throw new Error(`dashboard template placeholder missing: ${DASHBOARD_PLACEHOLDER}`);
  }
  const serialized = JSON.stringify(model).replaceAll("<", "\\u003c");
  const html = template.replace(DASHBOARD_PLACEHOLDER, serialized);

  writeFileSync(
    resolve(root, ".automation/dashboard.json"),
    `${JSON.stringify(model, null, 2)}\n`,
    "utf8",
  );
  writeFileSync(
    resolve(root, ".automation/file-index.json"),
    `${JSON.stringify(fileIndexFromModel(model), null, 2)}\n`,
    "utf8",
  );
  writeFileSync(resolve(root, "dashboard.html"), html, "utf8");

  return {
    root,
    dashboard_path: "dashboard.html",
    data_path: ".automation/dashboard.json",
    file_index_path: ".automation/file-index.json",
    model,
  };
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const rootPath = process.argv[2] ?? ".";
  try {
    const result = refreshDashboard(rootPath);
    console.log(
      JSON.stringify({
        dashboard_path: result.dashboard_path,
        data_path: result.data_path,
        updated_at: result.model.updated_at,
      }),
    );
  } catch (error) {
    console.error(error instanceof Error ? error.message : String(error));
    process.exit(1);
  }
}
