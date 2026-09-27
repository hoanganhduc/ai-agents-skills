"""Opt-in native boundary canaries: synthetic data only, no real secrets."""
import os
from pathlib import Path
import subprocess
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
