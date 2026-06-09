# slack Specialists Index

Project: slack

## Purpose

Route future agents to the narrowest specialist context in this project.

## Specialist Map

### Product Engineer

Directory:

- `specialists/product_engineer/`

Owns:

- repo-local product and implementation facts
- CLI/TUI contract, config, storage, installer, release, and verification constraints

Default files:

- `specialists/product_engineer/ROLE.md`

## Loading Rule

Load only the smallest file set needed for the task. Project specialist facts
override root generalist defaults for the same subject. Use `/home/ryan/AGENTS.md`
and root generalists for workspace behavior; do not recreate project-root
`AGENTS.md`, lowercase `subagents/`, or project-level `context/` directories.
