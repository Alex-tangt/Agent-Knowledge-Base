# -*- coding: utf-8 -*-
"""预注册判定标准（独立复核 line: wk-verify / 端口 8783）。

纪律：本文件在执行方（task-1 / spike-runner）产出任何结果**之前**写定。
跑完自己的隔离实例、产出自己的观测之后，才允许去读执行方的证据文件。

判定标准是唯一裁决依据：检查脚本只负责产出"观测"，由本文件的
`aggregate()` 把 probe 级判定机械地汇总成 check 级判定。

术语
- PASS：观测满足该 probe 的通过条件。
- FAIL：观测明确违反通过条件（有可复算证据）。
- INCONCLUSIVE（无法复核）：观测无法作出裁决（接口不存在 / 存储不可字节核对 /
  依赖被禁止的能力等），必须写明原因，不得算作 PASS。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List

# ---------------------------------------------------------------------------
# 冻结常量（预注册参数；跑之前定死，跑中不得调整）
# ---------------------------------------------------------------------------

CRITERIA_VERSION = "1.0.0"
PRE_REGISTERED_AT = "2026-10-10T14:20:00+08:00"  # 首次写定时间（Asia/Shanghai）

#: 形态复查用：外部改源文件后，等待"追平更新"的最长时限（秒）。
#: mvp-protocol.md M2 要求"通过事先登记的更新机制与时限读到新正文"——
#: 这里是复核方登记的时限；超时未见新正文即判"拷贝（快照）"。
UPDATE_WINDOW_S = 120

#: 形态复查用：轮询间隔（秒）。
UPDATE_POLL_S = 10

#: 隔离实例就绪等待上限（秒）。
SERVER_READY_TIMEOUT_S = 300

#: 隔离实例停机后，判定"端口已释放"的等待上限（秒）。
SERVER_STOP_TIMEOUT_S = 60

#: 检索/读取往返超时（秒）。
CALL_TIMEOUT_S = 120

#: 磁盘字节核对上限：超过该大小的文件不做子串扫描（避免病理开销）。
DISK_SCAN_MAX_FILE_BYTES = 64 * 1024 * 1024

#: git 对象核对上限（blob 个数），超出则记录截断。
GIT_BLOB_SCAN_LIMIT = 400

#: 唯一测试标记前缀；每次运行拼接 run_id，保证"唯一"。
MARKER_PREFIX = "OVVERIFY-RT-MARKER"

#: 隔离面（唯一允许写入的两个根，见 verify_env.assert_writable）。
ISOLATION = {
    "port": 8783,
    "host": "127.0.0.1",
    "service": "openviking-server (候选实现, 隔离实例)",
    "verify_root_env": "OV_VERIFY_ROOT",
    "verify_root_default": r"%USERPROFILE%\.openviking-mvp-verify",
    "cli_config": "ovcli.conf",
    "server_config": "ov.conf",
    "forbidden_ports": [8765, 8781, 8782],
    "forbidden_paths": [
        r"%USERPROFILE%\.config\opencode\knowledge",
        r"%USERPROFILE%\.config\opencode\opencode.json",
        r"%USERPROFILE%\.openviking",
        r"<main>\memory_agent\vector_db",
        r"<wk-spike>",
        r"<wk-host>",
    ],
    "cloud_models": "disabled (embedding provider=local, 无 vlm 段)",
}

# ---------------------------------------------------------------------------
# 五条检查与逐 probe 判定规则
# ---------------------------------------------------------------------------

CHECKS: List[Dict[str, Any]] = [
    {
        "id": "C1",
        "title": "原文往返：原样保存与读回（同时核磁盘字节与读取接口正文）",
        "protocol": "M1（mvp-protocol.md 第 15、39 行）；架构 §4 get/append（第 69-84 行）",
        "must": True,
        "probes": [
            {
                "id": "C1a",
                "what": "写入接口（content/write）落盘后，raw 原文读取接口返回的正文与输入逐字节一致",
                "pass_rule": "sha256(read_raw 结果 UTF-8 字节) == sha256(输入 UTF-8 字节)，且长度一致",
                "fail_rule": "两者 sha256 不同（附双方字节长度 + 首个分歧偏移）",
                "inconclusive_rule": "raw 读取接口不存在或调用失败且无法区分失败原因",
            },
            {
                "id": "C1b",
                "what": "磁盘上实际存下来的字节与输入一致（不依赖读取接口）",
                "pass_rule": "在隔离 workspace 内找到与输入 UTF-8 字节完全相等的持久化对象（普通文件或 git blob），并记录其路径/blob sha256",
                "fail_rule": "找到该内容的唯一持久化形态但与输入字节不等（附差异），或只找到被改写版本",
                "inconclusive_rule": "存储形态无法做字节核对（例：仅存于不可读的打包容器），须写明原因；不得默认 PASS",
            },
            {
                "id": "C1c",
                "what": "默认读回接口（content/read 非 raw）同样不得改写原文",
                "pass_rule": "sha256(默认 read 结果 UTF-8 字节) == sha256(输入 UTF-8 字节)",
                "fail_rule": "默认 read 返回的正文与输入不等（附差异）；此时即使 raw 一致也判 FAIL（M1 要求公开入口读回一致）",
                "inconclusive_rule": "默认读回接口不可用",
            },
            {
                "id": "C1d",
                "what": "（附加）服务重启后同一条目仍可读回同一原文",
                "pass_rule": "重启隔离服务后 read_raw 结果与输入 sha256 相同",
                "fail_rule": "重启后读不到或内容变化",
                "inconclusive_rule": "重启未执行（例：C5 停机阶段异常）",
            },
        ],
    },
    {
        "id": "C2",
        "title": "形态：接入的项目文件是「指过去」还是「拷一份进来」",
        "protocol": "M2（mvp-protocol.md 第 17、40 行）",
        "must": True,
        "probes": [
            {
                "id": "C2a",
                "what": "接入一个含 git 的测试目录后，外部修改源文件，登记时限（%ds 秒）内读到新正文" % UPDATE_WINDOW_S,
                "pass_rule": "修改后 ≤ 登记时限内 read 返回的正文 == 修改后源文件内容（=指过去/追平）",
                "fail_rule": "登记时限内读到的仍是修改前内容（=拷贝/快照，未追平）",
                "inconclusive_rule": "接入本身失败，或读不到接入后的条目",
            },
            {
                "id": "C2b",
                "what": "是否存在需要双向同步的第二份正文（拷贝的直接证据）",
                "pass_rule": "隔离 workspace 内不存在与源文件对应的独立正文副本；条目内容由源文件直接提供",
                "fail_rule": "workspace 内存在一份独立正文副本，且源改动不自动同步（附两处路径与 sha256）",
                "inconclusive_rule": "无法定位接入后的持久化正文位置",
            },
            {
                "id": "C2c",
                "what": "（附加）同一个源重复接入是否产生第二条条目（拷贝路径常见的重复面）",
                "pass_rule": "同一源重复接入后，目标位置条目数不增加（或显式 AlreadyExists/Conflict 报错）",
                "fail_rule": "同一源重复接入产生两条独立条目",
                "inconclusive_rule": "重复接入未被允许/未执行",
            },
        ],
    },
    {
        "id": "C3",
        "title": "重复提交：同一请求提交两次不得产生第二条",
        "protocol": "M1（mvp-protocol.md 第 19、39 行）；架构 §4 append(request_id)（第 84 行）",
        "must": True,
        "probes": [
            {
                "id": "C3a",
                "what": "同一 URI 同一正文 write 两次（mode=replace）后，该位置只有一条条目且正文正确",
                "pass_rule": "父目录 ls 中该名字出现恰好 1 次；read_raw 正文 == 输入",
                "fail_rule": "出现 ≥2 条同名字条目，或正文被第二次写入以外的因素改变",
                "inconclusive_rule": "ls/read 不可用",
            },
            {
                "id": "C3b",
                "what": "同一 URI 用 mode=create 重复提交：第二次必须幂等或显式冲突，不得产生第二条",
                "pass_rule": "第二次返回成功但条目仍为 1 条，或第二次显式报错（AlreadyExists/Conflict/InvalidArgument），条目仍为 1 条",
                "fail_rule": "出现 2 条条目（例：自动改名/自动编号）或静默产生第二份内容",
                "inconclusive_rule": "第二次调用结果无法判定（超时/接口缺失）",
            },
            {
                "id": "C3c",
                "what": "add_resource 同一源重复提交（不指定显式 to）是否产生第二条",
                "pass_rule": "重复提交后目标父目录条目数不增加，或显式 AlreadyExists/Conflict",
                "fail_rule": "重复提交新增了第二条独立条目",
                "inconclusive_rule": "add_resource 在本隔离实例不可用",
            },
            {
                "id": "C3d",
                "what": "（附加）公开写入接口是否提供 request_id / 幂等键",
                "pass_rule": "存在可传入的幂等键且服务端据此判重",
                "fail_rule": "公开写入接口（content/write、resources）无任何幂等键字段（以框架源码字段集为证）",
                "inconclusive_rule": "无法静态确认字段集",
            },
        ],
    },
    {
        "id": "C4",
        "title": "来源只读：接入前后源目录清单与提交号一致；通过基座写来源被拒",
        "protocol": "M2（mvp-protocol.md 第 19、40 行）",
        "must": True,
        "probes": [
            {
                "id": "C4a",
                "what": "接入前后源目录（含 git）的文件清单、逐文件 sha256、git 提交号完全一致",
                "pass_rule": "前后快照逐项相等（文件集合、每文件 sha256、git rev-parse HEAD、git status --porcelain 均相同）",
                "fail_rule": "任意一项变化（附 diff）",
                "inconclusive_rule": "源目录快照无法取得（git 不可用等）",
            },
            {
                "id": "C4b",
                "what": "通过基座写入该来源是否被拒绝",
                "pass_rule": "基座对来源路径的写请求被显式拒绝（PermissionDenied/ReadOnly/类似明确错误码）",
                "fail_rule": "基座写请求被接受并改变来源内容（附写响应 + 源目录 diff）",
                "inconclusive_rule": "基座完全不持有来源路径（只有导入副本）——此时\"写来源\"在物理上不可达，须照实记录，不得记为 PASS",
            },
            {
                "id": "C4c",
                "what": "（附加）以本地文件系统路径作为资源来源的 HTTP 请求被显式拒绝",
                "pass_rule": "显式错误（PermissionDenied / 4xx，含可读错误码），且未导入任何内容",
                "fail_rule": "请求被接受并导入了本地路径内容",
                "inconclusive_rule": "接口不存在",
            },
        ],
    },
    {
        "id": "C5",
        "title": "停机报错：停隔离服务后消费者收到显式不可用错误，而非静默空结果",
        "protocol": "M4（mvp-protocol.md 第 42 行）；架构 §4 status/明确错误（第 88 行）",
        "must": True,
        "probes": [
            {
                "id": "C5a",
                "what": "服务在线时同一消费者调用成功（作为停机对照）",
                "pass_rule": "read_raw 成功且正文与输入一致",
                "fail_rule": "在线时调用即失败（说明对照组无效）",
                "inconclusive_rule": "在线调用未执行",
            },
            {
                "id": "C5b",
                "what": "只停隔离服务后，读取调用抛出显式错误（不是返回空/None/空列表）",
                "pass_rule": "抛出网络错误或 UnavailableError（含非空 message），且**未**返回任何貌似成功的结果",
                "fail_rule": "返回空字符串/空列表/None 且未抛错（=静默空结果）",
                "inconclusive_rule": "调用挂起至超时（须记录实际耗时），无法区分静默与不可用",
            },
            {
                "id": "C5c",
                "what": "只停隔离服务后，检索调用同样显式报错",
                "pass_rule": "抛出显式错误；未返回空命中列表",
                "fail_rule": "返回空命中列表且未抛错",
                "inconclusive_rule": "调用挂起至超时",
            },
            {
                "id": "C5d",
                "what": "（附加）错误可区分：无匹配 / 拒绝访问 / 服务不可用",
                "pass_rule": "不存在条目返回 NotFound 类错误、越权返回 PermissionDenied 类错误、停机返回 Unavailable/网络错误，三者类型不同",
                "fail_rule": "三类中任意两类塌缩为同一结果（如都返回空）",
                "inconclusive_rule": "缺少可用身份去构造越权调用（本实例 dev/ROOT 身份无法构造越权）",
            },
        ],
    },
]


def canonical_json(obj: Any) -> str:
    """稳定序列化（键排序 + 无多余空白），用于可复算指纹。"""
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def criteria_payload() -> Dict[str, Any]:
    return {
        "criteria_version": CRITERIA_VERSION,
        "pre_registered_at": PRE_REGISTERED_AT,
        "isolation": ISOLATION,
        "params": {
            "UPDATE_WINDOW_S": UPDATE_WINDOW_S,
            "UPDATE_POLL_S": UPDATE_POLL_S,
            "SERVER_READY_TIMEOUT_S": SERVER_READY_TIMEOUT_S,
            "SERVER_STOP_TIMEOUT_S": SERVER_STOP_TIMEOUT_S,
            "CALL_TIMEOUT_S": CALL_TIMEOUT_S,
            "MARKER_PREFIX": MARKER_PREFIX,
        },
        "checks": CHECKS,
    }


def criteria_sha256() -> str:
    """判定标准指纹：任何改动都会改变它（用于"跑前预注册、跑后未改"的证明）。"""
    return hashlib.sha256(canonical_json(criteria_payload()).encode("utf-8")).hexdigest()


def aggregate(check_id: str, probe_verdicts: Dict[str, str]) -> str:
    """把 probe 级判定机械汇总为 check 级判定。

    PASS  <=> 该 check 的所有 probe 全为 PASS
    FAIL  <=> 任一 probe 为 FAIL
    INCONCLUSIVE <=> 无 FAIL 且至少一个 INCONCLUSIVE
    """
    check = next(c for c in CHECKS if c["id"] == check_id)
    expected = [p["id"] for p in check["probes"]]
    missing = [p for p in expected if p not in probe_verdicts]
    if missing:
        raise ValueError(f"{check_id}: 缺少 probe 判定: {missing}")
    verdicts = [probe_verdicts[p] for p in expected]
    allowed = {"PASS", "FAIL", "INCONCLUSIVE"}
    bad = [v for v in verdicts if v not in allowed]
    if bad:
        raise ValueError(f"{check_id}: 非法判定 {bad}")
    if "FAIL" in verdicts:
        return "FAIL"
    if "INCONCLUSIVE" in verdicts:
        return "INCONCLUSIVE"
    return "PASS"


def structural_audit() -> List[str]:
    """结构自检：返回问题列表（空 = 通过）。静态阶段使用。"""
    problems: List[str] = []
    ids = [c["id"] for c in CHECKS]
    if len(ids) != len(set(ids)):
        problems.append("check id 重复")
    for c in CHECKS:
        for key in ("id", "title", "protocol", "must", "probes"):
            if key not in c:
                problems.append(f"{c.get('id')}: 缺字段 {key}")
        seen = set()
        for p in c.get("probes", []):
            for key in ("id", "what", "pass_rule", "fail_rule", "inconclusive_rule"):
                if key not in p or not str(p[key]).strip():
                    problems.append(f"{c['id']}/{p.get('id')}: 判定标准缺字段 {key}")
            if p["id"] in seen:
                problems.append(f"{c['id']}: probe id 重复 {p['id']}")
            seen.add(p["id"])
    if ISOLATION["port"] in ISOLATION["forbidden_ports"]:
        problems.append("隔离端口落在禁止端口集合内")
    return problems


if __name__ == "__main__":
    print(canonical_json(criteria_payload())[:400])
    print("criteria_sha256 =", criteria_sha256())
    print("structural problems =", structural_audit())
