// Keep bounded tmpfs outputs available until the supervisor pauses, collects,
// and destroys the container. Compilation never gets a writable host bind.
import fs from "node:fs";
import { spawn } from "node:child_process";
const mode = process.argv[2];
if (fs.existsSync("/input/work")) fs.cpSync("/input/work", "/work", { recursive: true, dereference: false });
const child = spawn("node", ["/adapter/lax_adapter.mjs", mode], { stdio: "inherit" });
child.on("error", (error) => {
  fs.writeFileSync("/result/.finished.json", JSON.stringify({ mode, exit: 127 }));
  console.error(error.message);
});
child.on("close", (code) => {
  fs.writeFileSync("/result/.finished.json", JSON.stringify({ mode, exit: code ?? 128 }));
});
setInterval(() => {}, 1000);
