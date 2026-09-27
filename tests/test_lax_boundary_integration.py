"""Opt-in native boundary canaries: synthetic data only, no real secrets."""
import os
import json
from pathlib import Path
import subprocess
import signal
import sys
sys.dont_write_bytecode = True
import tempfile
import time
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "canonical/runtime/skills/lax-formalization"))
from lax_formalization import container_options
from lax_executor import HERE, settings


@unittest.skipUnless(os.environ.get("AAS_LAX_EXECUTOR_TEST") == "1", "explicit native Docker boundary test")
class NativeBoundaryTests(unittest.TestCase):
    def test_main_thread_termination_removes_owned_container(self):
        cfg = settings()
        with tempfile.TemporaryDirectory(prefix="aas-lax-signal-") as tmp:
            root = Path(tmp); job = root / "job"; (job / "control").mkdir(parents=True)
            adapter = root / "adapter"; adapter.mkdir()
            (adapter / "container_entry.mjs").write_text(
                "import fs from 'node:fs';fs.writeFileSync('/result/ready','ready');setInterval(()=>{},1000);",
                encoding="utf-8")
            config = root / "config.json"; config.write_text(json.dumps(cfg), encoding="utf-8")
            script = """
import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import lax_executor as e
e.HERE = Path(sys.argv[2])
with e.verification_termination_guard():
    e.phase(json.loads(Path(sys.argv[3]).read_text()), 'static', Path(sys.argv[4]), [], timeout=30)
"""
            proc = subprocess.Popen([sys.executable, "-c", script, str(HERE), str(adapter), str(config), str(job)],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            owned = []
            try:
                for _ in range(60):
                    rows = subprocess.check_output(["docker", "ps", "--filter", "name=aas-lax-", "--format", "{{.ID}}"], encoding="utf-8")
                    for cid in rows.split():
                        info = json.loads(subprocess.check_output(["docker", "inspect", cid]))[0]
                        if any(m.get("Source") == str(job / "control") for m in info["Mounts"]):
                            owned = [cid]
                            break
                    if owned:
                        ready = subprocess.run(["docker", "exec", owned[0], "cat", "/result/ready"], capture_output=True, timeout=5)
                        if ready.returncode == 0: break
                    self.assertIsNone(proc.poll())
                    time.sleep(.1)
                else: self.fail("signal canary did not become ready")
                proc.send_signal(signal.SIGTERM)
                self.assertEqual(proc.wait(timeout=15), 128 + signal.SIGTERM)
                remaining = subprocess.check_output(["docker", "ps", "--all", "--quiet", "--filter", "id=" + owned[0]])
                self.assertFalse(remaining.strip())
            finally:
                if proc.poll() is None:
                    proc.kill(); proc.wait(timeout=10)
                for cid in owned:
                    subprocess.run(["docker", "rm", "--force", cid], capture_output=True, timeout=15)

    def test_private_paths_readonly_input_network_and_descendant_teardown(self):
        cfg = settings()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); source = root / "source"; source.mkdir()
            private = root / "private-canary"; private.write_text("synthetic-only", encoding="utf-8")
            name = "aas-boundary-" + uuid.uuid4().hex
            options = container_options(name, cfg["image"], [(source, "/source", False), (HERE, "/adapter", False)], writable=False)
            script = r'''
const fs=require('node:fs'),net=require('node:net'),{spawn}=require('node:child_process');
if(fs.existsSync(process.argv[1])||fs.existsSync('/var/run/docker.sock'))process.exit(21);
try{fs.writeFileSync('/source/forbidden','x');process.exit(22);}catch(e){if(e.code!=='EROFS'&&e.code!=='EACCES')throw e;}
const s=net.createConnection({host:'1.1.1.1',port:80});
s.on('connect',()=>process.exit(23));s.on('error',()=>{});setTimeout(()=>s.destroy(),300);
spawn('node',['-e',"const fs=require('node:fs');process.kill(1,'SIGUSR1');setInterval(()=>fs.writeFileSync('/result/child',Date.now().toString()),10)"],{stdio:'ignore'});
setTimeout(()=>fs.writeFileSync('/result/ready','ready'),500);
setInterval(()=>{},1000);
'''
            proc = subprocess.Popen([*options, "node", "--disable-sigusr1", "-e", script, str(private)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                for _ in range(30):
                    ready = subprocess.run(["docker", "exec", name, "cat", "/result/ready"], capture_output=True, timeout=5)
                    if ready.returncode == 0: break
                    self.assertIsNone(proc.poll(), "isolation canary failed")
                    time.sleep(.1)
                else: self.fail("canary readiness timeout")
                probe = "const n=require('node:net');let s=n.createConnection({host:'127.0.0.1',port:9229});s.on('connect',()=>process.exit(1));s.on('error',()=>process.exit(0));setTimeout(()=>process.exit(0),300)"
                subprocess.run(["docker", "exec", name, "node", "--disable-sigusr1", "-e", probe], check=True, timeout=5)
                stopped = subprocess.check_output(["docker", "exec", name, "node", "--disable-sigusr1", "/adapter/stop_children.mjs"], timeout=10)
                self.assertEqual(stopped.strip(), b"descendants-stopped")
                read = ["docker", "exec", name, "cat", "/result/child"]
                before = subprocess.check_output(read, timeout=5)
                time.sleep(.2)
                self.assertEqual(subprocess.check_output(read, timeout=5), before)
                self.assertFalse((source / "forbidden").exists())
            finally:
                subprocess.run(["docker", "rm", "--force", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
                proc.wait(timeout=10)


if __name__ == "__main__": unittest.main()
