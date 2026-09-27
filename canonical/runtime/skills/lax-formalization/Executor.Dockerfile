ARG BASE_IMAGE=node:22-bookworm-slim@sha256:25330af3531fb5e23318554a0aa911125b6e91b1b777edf7655501d207c067a2
FROM ${BASE_IMAGE}
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates build-essential \
    && rm -rf /var/lib/apt/lists/*
LABEL org.ai-agents-skills.executor="lax-0.1.48-v1"
