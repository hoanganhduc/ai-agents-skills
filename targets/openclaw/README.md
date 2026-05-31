# OpenClaw Target

OpenClaw is an explicit fake-root-only target until native target evidence is
recorded. The installer must not plan or apply writes under a real `.openclaw`
tree from normal install flows.

OpenClaw receives only the artifacts that are safe for the fake-root target.
Runtime-backed skills are blocked unless neutral runtime evidence exists, and
instruction blocks remain disabled. Use the OpenClaw inventory, manifest, and
evidence commands for any future native-target promotion.
