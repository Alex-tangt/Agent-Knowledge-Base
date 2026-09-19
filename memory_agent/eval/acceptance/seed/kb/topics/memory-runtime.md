---
id: topics/memory-runtime
title: "记忆检索运行时"
type: topic
tags: [retrieval, index]
status: current
updated: 2026-09-19
---

# 记忆检索运行时

本地平面的默认词法路是 BM25，融合用 DBSF；派生索引是「代目录 + CURRENT 指针」，
全量重建换新代、原子切指针。
