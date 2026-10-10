# M4 · 不可用与处理失败可见

- 结论：**PASS**
- 生成时间：2026-10-10T14:42:51+0800
- 运行 profile：`authz`（端口 8781）

| 检查 | 结论 |
|---|---|
| M4.1-baseline-up 服务在线时读取正常、无匹配可识别 | **PASS** |
| M4.2-service-unavailable-explicit 只停隔离服务后，消费者收到明确的不可用错误 | **PASS** |
| M4.3-three-way-distinguishable 无匹配 / 拒绝访问 / 服务不可用 三者可区分 | **PASS** |
| M4.4-recovery-after-restart 重启隔离服务后同一引用恢复可读 | **PASS** |
| M4.5-processing-failure-visible 附带处理失败的可见状态（导入含不可解析文件） | **PASS** |
| M4.6-derived-status-not-proof 派生处理状态是否可信（实测并登记） | **PASS** |
| M4.7-followup-paths 失败后的后续处理入口存在且可用 | **PASS** |

## 三类可区分信号

```json
{
  "no_match": {
    "observed": {
      "http_status": 200,
      "empty_result": true
    },
    "classification": "no_match（HTTP 200 + 空结果）",
    "source": "M4.1"
  },
  "access_denied": {
    "observed": {
      "http_status": 403,
      "code": "PERMISSION_DENIED"
    },
    "classification": "access_denied（403 PERMISSION_DENIED + 具体资源 URI）",
    "source": "M3.6（在线时实测）"
  },
  "service_unavailable": {
    "observed": {
      "http_status": 0,
      "cli_returncode": 1
    },
    "classification": "service_unavailable（无 HTTP 状态码 / CLI 非 0）",
    "source": "M4.2"
  }
}
```

## 派生状态不可信（已知限制）

```json
{
  "write_response": {
    "written_bytes": 47,
    "semantic_status": "complete",
    "vector_status": "complete",
    "queue_status": {
      "Semantic": {
        "processed": 1,
        "requeue_count": 0,
        "error_count": 0,
        "errors": []
      },
      "Embedding": {
        "processed": 3,
        "requeue_count": 0,
        "error_count": 0,
        "errors": []
      }
    }
  },
  "directory_overview": "# note\n\n[Directory overview is not ready]",
  "directory_abstract": "[Directory overview is not ready]",
  "placeholder_written": true,
  "conclusion": "`semantic_status=complete` 且 `Semantic.error_count=0`，但目录 abstract/overview 实际是 『[Directory overview is not ready]』占位 —— 未配置 VLM 时派生内容静默降级，状态字段不构成『派生内容已生成』的证据。",
  "limitation": "派生内容失败/降级没有独立可见信号；只能读 abstract/overview 正文自行判断。"
}
```

## 复现

```powershell
$env:SPIKE_PROFILE='authz'; python spike_server.py start
python m4_failure.py
```
