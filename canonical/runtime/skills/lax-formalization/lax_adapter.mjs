// Version-bound adapter: validator/inspector judgments remain upstream Lax code.
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";

const pkg = "/lax-package";
const load = (name) => import(pathToFileURL(path.join(pkg, "dist", name)));
const { ArchiveSnapshot } = await load("submission-validation/archive/snapshot.js");
const { runStaticValidation } = await load("submission-validation/phases/static.js");
const { runResolution } = await load("submission-validation/phases/resolution.js");
const { hostValidationRuntime } = await load("submission-validation/pins.js");
const { validateSubmissionOnHost } = await load("submission-validation/host/pipeline.js");
const { hostLeanEnv } = await load("submission-validation/host/leanenv.js");
const { inspectorBinary } = await load("submission-validation/host/inspector.js");
const { environment } = await load("submission-validation/environments.js");
const { parseInspectorReport } = await load("submission-validation/phases/inspect-runner.js");
const { judgeInspection } = await load("submission-validation/phases/inspect.js");
const { warmDir } = await load("submission-validation/host/warmstore.js");

const [mode] = process.argv.slice(2);
const spec = JSON.parse(fs.readFileSync("/control/spec.json", "utf8"));
const selected = environment(spec.environment);
if (!selected || JSON.parse(fs.readFileSync(`${pkg}/package.json`, "utf8")).version !== "0.1.48") {
  throw new Error("unqualified Lax version/environment");
}
fs.mkdirSync(process.env.HOME, { recursive: true });

function inspectSource(root) {
  const archive = new ArchiveSnapshot("/database", spec.archiveSha);
  const stat = runStaticValidation(spec.request, root, (entry) => hostValidationRuntime(entry));
  if (stat.findings.failed) throw new Error(JSON.stringify(stat.findings.violations));
  const resolution = runResolution(spec.request, stat.result, archive, stat.runtime);
  if (resolution.findings.failed) throw new Error(JSON.stringify(resolution.findings.violations));
  return { stat, resolution, archive };
}

function save(name, value) {
  fs.writeFileSync(`/result/${name}.json`, `${JSON.stringify(value, null, 2)}\n`, { flag: "wx", mode: 0o600 });
}

if (mode === "prepare") {
  const bin = await inspectorBinary(selected, { echo: true });
  save("prepared", { inspector: bin, environment: selected });
} else if (mode === "static") {
  const { stat, resolution } = inspectSource(`/source/${spec.folder}`);
  save("static", { manifest: stat.result.manifest, resolution: resolution.result,
    inventories: Object.fromEntries(["concepts", "proofs"].map((kind) => [kind, {
      ...stat.result[kind].inventory, paths: Object.fromEntries(stat.result[kind].inventory.paths),
    }])), runtime: stat.runtime });
} else if (mode === "compile" || mode === "concepts") {
  const root = `/work/repo/${spec.folder}`;
  const { archive } = inspectSource(root);
  // Exact dependency checkouts, including a minimal local Git context, were
  // admitted and preseeded by the supervisor. No network is available here.
  const report = await validateSubmissionOnHost(spec.request, "/work/job", {
    local: { fetched: { repositoryRoot: "/work/repo", submissionRoot: root }, archive },
    replay: true, scope: mode === "concepts" ? "concepts" : "both", nonstrict: false, echo: true,
  });
  save("compile", report);
  if (!report.ok || (mode === "compile" && !report.buildOutput)) process.exitCode = 1;
} else if (mode === "check") {
  const { stat, resolution } = inspectSource(`/source/${spec.folder}`);
  const bin = await inspectorBinary(selected);
  const reports = {};
  for (const kind of ["concepts", "proofs"]) {
    const inv = stat.result[kind].inventory;
    const own = kind === "proofs" ? ["/capture/proofs/lib", "/capture/concepts/lib"] : ["/capture/concepts/lib"];
    const depLibs = Object.values(spec.dependencyLibs || {});
    const lean = hostLeanEnv(selected, own, depLibs, warmDir(selected), 2);
    const replay = await lean.exec(lean.leancheckerBin, [inv.rootModule], `/capture/${kind}/package`);
    if (replay.code !== 0) throw new Error(`fresh ${kind} replay failed: ${replay.output}`);
    const output = `/result/${kind}-inspection.json`;
    const inspected = await lean.exec(bin, [output, inv.rootModule, ...inv.modules], `/capture/${kind}/package`);
    if (inspected.code !== 0) throw new Error(`fresh ${kind} inspection failed: ${inspected.output}`);
    reports[kind] = parseInspectorReport(JSON.parse(fs.readFileSync(output, "utf8")));
  }
  const judged = judgeInspection(reports.concepts, reports.proofs,
    stat.result.concepts.inventory, stat.result.proofs.inventory, resolution.result, "both");
  save("checked", { ok: !judged.findings.failed, violations: judged.findings.violations,
    warnings: judged.findings.warnings, inspection: judged.result,
    coverage: Object.fromEntries(["concepts", "proofs"].map((kind) => [kind, stat.result[kind].inventory.modules])) });
  if (judged.findings.failed) process.exitCode = 1;
} else {
  throw new Error("unsupported adapter operation");
}
