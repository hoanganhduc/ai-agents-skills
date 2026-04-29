# Modal Offload Routing

Use this guidance when a task may exceed local CPU, memory, disk, or GPU
capacity.

Keep work local when:

- data is small enough for the current machine
- credentials or private data should not leave the machine
- local verification is faster than setup overhead

Consider offload when:

- the task is embarrassingly parallel
- GPU or high-memory CPU is required
- a dry-run sample proves the job is too slow locally
- the user has configured remote compute credentials outside this repo

Before offload, run a resource check and a small local sample when possible.
Never print or copy remote credentials into prompts, logs, docs, or managed
repo files.
