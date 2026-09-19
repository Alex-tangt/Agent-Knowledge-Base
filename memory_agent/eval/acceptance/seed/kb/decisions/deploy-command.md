---
id: decisions/deploy-command
title: "一键部署命令"
type: decision
tags: [deployment, install]
status: current
updated: 2026-09-19
---

# 一键部署命令

部署 memory-agent：Linux / WSL 用 `bash install.sh`；Windows 用 `pwsh install.ps1`。
两者都是薄壳，调用同一个入口 `memory-agent install`（幂等，可重复跑）。
