// Invoked by the outside supervisor after the phase reports completion.
// Only this collector and the trusted PID 1 holder may remain runnable.
import fs from "node:fs";
import { setTimeout } from "node:timers/promises";
let survivors = [];
for (let attempt = 0; attempt < 100; attempt++) {
  survivors = [];
  for (const name of fs.readdirSync("/proc")) {
    if (!/^\d+$/.test(name)) continue;
    const pid = Number(name);
    if (pid === 1 || pid === process.pid) continue;
    try {
      const data = fs.readFileSync(`/proc/${pid}/stat`, "utf8");
      if (data.slice(data.lastIndexOf(")") + 2, data.lastIndexOf(")") + 3) === "Z") continue;
      survivors.push(pid);
      process.kill(pid, "SIGKILL");
    } catch (error) {
      if (error.code !== "ENOENT" && error.code !== "ESRCH") throw error;
    }
  }
  if (survivors.length === 0) break;
  await setTimeout(10);
}
if (survivors.length) throw new Error("compilation descendants did not terminate");
process.stdout.write("descendants-stopped\n");
