"""#18 复现：local_files_only=True 下是否仍发外部 HF 请求，以及离线开关的代价。

驱动 = 依次用不同环境跑 `load_probe.py`（子进程，环境干净），收集：
  - 是否出现对外 HTTP 请求（grep stderr 的 `HTTP Request`）
  - 加载成功/失败与耗时（`PROBE_RESULT`）
  - 加载完成后是否 ready

变体：
  baseline         无额外 env（真实网络/VPN）
  unreachable      HF_ENDPOINT 指向不可路由地址（模拟弱网/代理挂）
  unreachable+off  HF_ENDPOINT + HF_HUB_OFFLINE=1
  offline          HF_HUB_OFFLINE=1

结论写 results.md。脚本只读，不写任何项目状态。
"""
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PROBE = os.path.join(HERE, "load_probe.py")
PY = sys.executable

BASE_ENV = {
    k: v
    for k, v in os.environ.items()
    if k.upper() not in {"HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"}
}

# (name, env, timeout_sec)。unreachable 变体用短超时：足以证明「远慢于 ~40s 就绪」，
# 不必等到 240s。
VARIANTS = [
    ("baseline", {}, 180),
    ("unreachable", {"HF_ENDPOINT": "http://10.255.255.1"}, 90),
    ("unreachable+off", {"HF_ENDPOINT": "http://10.255.255.1", "HF_HUB_OFFLINE": "1"}, 180),
    ("offline", {"HF_HUB_OFFLINE": "1"}, 180),
]

REQ_RE = re.compile(r"HTTP Request: (\w+) (\S+) \"([^\"]+)\"")
RES_RE = re.compile(r"PROBE_RESULT (\w+)\t(\w+)\t([\d.]+)s(?:\t(.*))?")


def run(target, extra, timeout=240):
    env = dict(BASE_ENV)
    env.update(extra)
    t0 = time.perf_counter()
    try:
        proc = subprocess.run(
            [PY, PROBE, target],
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            cwd=HERE,
        )
        out, code, timed_out = proc.stderr or "", proc.returncode, False
    except subprocess.TimeoutExpired as exc:
        out = (exc.stderr or b"").decode("utf-8", "replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        code, timed_out = None, True
    elapsed = time.perf_counter() - t0

    requests = [{"method": meth, "url": url, "status": st} for meth, url, st in REQ_RE.findall(out)]
    m = RES_RE.search(out)
    result = None
    if m:
        result = {"status": m.group(1), "target": m.group(2), "load_sec": float(m.group(3))}
        if m.group(4):
            result["error"] = m.group(4)
    return {
        "requests": requests,
        "n_requests": len(requests),
        "result": result,
        "returncode": code,
        "timed_out": timed_out,
        "elapsed_sec": round(elapsed, 2),
    }


def main():
    targets = sys.argv[1:] or ["embed"]
    out_path = os.path.join(HERE, "probe_results.json")
    report = {}
    if os.path.exists(out_path):
        with open(out_path, encoding="utf-8") as f:
            report = json.load(f)
    for target in targets:
        report.setdefault(target, {})
        for name, extra, timeout in VARIANTS:
            print(f"[{target}] {name} ...", file=sys.stderr)
            report[target][name] = run(target, extra, timeout=timeout)
            r = report[target][name]
            print(
                f"  -> n_requests={r['n_requests']} result={r['result']} "
                f"rc={r['returncode']} timed_out={r['timed_out']} elapsed={r['elapsed_sec']}s",
                file=sys.stderr,
            )
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"\nwrote {out_path}", file=sys.stderr)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
