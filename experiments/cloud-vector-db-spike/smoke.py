"""#25 云向量库真实 smoke：建集合 → upsert → 按 tenant 过滤检索。

证据脚本，不是产品代码。只从进程环境 / `MEMORY_ENV_FILE` 读凭据，**绝不打印**。
用法：
    $env:MEMORY_ENV_FILE = "<repo>/memory_agent/.env"
    <repo>/venv/Scripts/python.exe smoke.py --suite all
    <repo>/venv/Scripts/python.exe smoke.py --cleanup

依赖 `pymilvus`（运维工具，不进 requirements.txt）。
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys
import time

DIM = 1024
TENANTS = ["tenant-a", "tenant-b", "tenant-c"]
PER_TENANT = 4


def log(msg: str) -> None:
    print(msg, flush=True)


SECRET_ENV_KEYS = ("MEMORY_CLOUD_USER", "MEMORY_CLOUD_PASSWORD", "MEMORY_CLOUD_TOKEN")


def redact(obj):
    """把凭据值从证据里抹掉：服务端错误消息可能回显 DB 用户名。"""
    secrets = [os.environ.get(key) for key in SECRET_ENV_KEYS]
    secrets = [s for s in secrets if s]

    def walk(value):
        if isinstance(value, dict):
            return {k: walk(v) for k, v in value.items()}
        if isinstance(value, list):
            return [walk(v) for v in value]
        if isinstance(value, str):
            for secret in secrets:
                value = value.replace(secret, "<redacted>")
            return value
        return value

    return walk(obj)


def load_env() -> None:
    env_file = os.environ.get("MEMORY_ENV_FILE")
    if not env_file:
        return
    try:
        from dotenv import load_dotenv
    except ImportError:  # pragma: no cover
        return
    load_dotenv(env_file, override=False)


def creds() -> dict:
    uri = os.environ.get("MEMORY_CLOUD_URI")
    if not uri:
        raise SystemExit("MEMORY_CLOUD_URI 未设置（见 memory_agent/.env.example）")
    token = os.environ.get("MEMORY_CLOUD_TOKEN")
    user = os.environ.get("MEMORY_CLOUD_USER")
    password = os.environ.get("MEMORY_CLOUD_PASSWORD")
    out = {"uri": uri}
    if token:
        out["token"] = token
    elif user and password:
        out["user"] = user
        out["password"] = password
    else:
        raise SystemExit("需要 MEMORY_CLOUD_TOKEN 或 MEMORY_CLOUD_USER+MEMORY_CLOUD_PASSWORD")
    return out


def make_client(db_name: str = "default"):
    from pymilvus import MilvusClient

    return MilvusClient(**creds(), db_name=db_name)


def vector_for(tenant: str, i: int) -> list[float]:
    """确定性向量：按 tenant 哈希铺满前若干维，便于断言过滤正确性。"""
    seed = hashlib.sha256(f"{tenant}:{i}".encode()).digest()
    base = [((b / 255.0) - 0.5) for b in seed]
    vec = (base * (DIM // len(base) + 1))[:DIM]
    norm = sum(v * v for v in vec) ** 0.5 or 1.0
    return [v / norm for v in vec]


def q(tenant: str) -> list[float]:
    return vector_for(tenant, 0)


def rows():
    out = []
    idx = 0
    for tenant in TENANTS:
        for i in range(PER_TENANT):
            out.append(
                {
                    "id": idx,
                    "vector": vector_for(tenant, i),
                    "tenant": tenant,
                    "text": f"{tenant} doc {i}",
                }
            )
            idx += 1
    return out


# ---------------------------------------------------------------- suites


def suite_rbac(client) -> dict:
    """只读探测 DB 级 RBAC 能力：能否 list / describe 用户与角色。"""
    res: dict = {}
    for label, fn in (
        ("list_users", lambda: client.list_users()),
        ("list_roles", lambda: client.list_roles()),
    ):
        try:
            res[label] = fn()
        except Exception as exc:  # noqa: BLE001 - 证据要原始错误
            res[label] = f"ERROR: {type(exc).__name__}: {exc}"
    # 角色详情：对 list_roles 的每个角色尝试 describe_role
    roles = res.get("list_roles")
    if isinstance(roles, list):
        details = {}
        for role in roles:
            try:
                details[str(role)] = client.describe_role(str(role))
            except Exception as exc:  # noqa: BLE001
                details[str(role)] = f"ERROR: {type(exc).__name__}: {exc}"
        res["describe_role"] = details
    return res


def suite_filter(client, name: str) -> dict:
    """共享集合 + tenant 行级过滤。"""
    from pymilvus import DataType

    res: dict = {"collection": name}
    if client.has_collection(name):
        client.drop_collection(name)

    schema = client.create_schema(auto_id=False, enable_dynamic_field=False)
    schema.add_field("id", DataType.INT64, is_primary=True)
    schema.add_field("vector", DataType.FLOAT_VECTOR, dim=DIM)
    schema.add_field("tenant", DataType.VARCHAR, max_length=64)
    schema.add_field("text", DataType.VARCHAR, max_length=256)
    index_params = client.prepare_index_params()
    index_params.add_index(field_name="vector", index_type="AUTOINDEX", metric_type="COSINE")
    index_params.add_index(field_name="tenant", index_type="INVERTED")

    t0 = time.perf_counter()
    client.create_collection(name, schema=schema, index_params=index_params)
    res["create_ms"] = round((time.perf_counter() - t0) * 1000)

    data = rows()
    client.insert(name, data)
    client.flush(name)
    # upsert 语义验证：覆盖 id=0
    client.upsert(name, [{"id": 0, "vector": vector_for("tenant-a", 99), "tenant": "tenant-a", "text": "upserted"}])
    client.flush(name)
    res["num_entities"] = client.query(name, filter="id >= 0", output_fields=["count(*)"])[0]["count(*)"]

    def search(flt, limit=10, **kw):
        t = time.perf_counter()
        hits = client.search(name, data=[q("tenant-a")], filter=flt, limit=limit,
                             output_fields=["tenant", "text"], **kw)
        return hits[0], round((time.perf_counter() - t) * 1000)

    hits_a, ms = search('tenant == "tenant-a"')
    res["filter_tenant_a"] = {"returned": len(hits_a), "ms": ms,
                              "tenants": sorted({h["entity"]["tenant"] for h in hits_a}),
                              "top": hits_a[0]["entity"]["text"] if hits_a else None}
    hits_none, _ = search("id >= 0")
    res["no_filter"] = {"returned": len(hits_none),
                        "tenants": sorted({h["entity"]["tenant"] for h in hits_none})}
    hits_wrong, _ = search('tenant == "tenant-b"')
    res["filter_tenant_b"] = {"tenants": sorted({h["entity"]["tenant"] for h in hits_wrong})}
    # 显式越权尝试：检索里带 tenant-b 过滤但目标向量是 A —— 看是否泄漏
    hits_leak, _ = search('tenant in ["tenant-b", "tenant-c"]')
    res["filter_other"] = {"tenants": sorted({h["entity"]["tenant"] for h in hits_leak})}
    return res


def suite_partition(client, name: str) -> dict:
    """物理分区隔离：collection 级自定义分区，检索可限定 partition_names。"""
    from pymilvus import DataType

    res: dict = {"collection": name}
    if client.has_collection(name):
        client.drop_collection(name)
    schema = client.create_schema(auto_id=False, enable_dynamic_field=False)
    schema.add_field("id", DataType.INT64, is_primary=True)
    schema.add_field("vector", DataType.FLOAT_VECTOR, dim=DIM)
    schema.add_field("tenant", DataType.VARCHAR, max_length=64)
    schema.add_field("text", DataType.VARCHAR, max_length=256)
    index_params = client.prepare_index_params()
    index_params.add_index(field_name="vector", index_type="AUTOINDEX", metric_type="COSINE")
    client.create_collection(name, schema=schema, index_params=index_params)

    for tenant in TENANTS:
        client.create_partition(name, tenant)
        part_rows = [r for r in rows() if r["tenant"] == tenant]
        client.insert(name, part_rows, partition_name=tenant)
    client.flush(name)
    res["partitions"] = client.list_partitions(name)
    res["stats"] = {p: client.get_partition_stats(name, p) for p in client.list_partitions(name)}

    def search(flt=None, partition_names=None):
        hits = client.search(name, data=[q("tenant-a")], filter=flt or "",
                             limit=10, output_fields=["tenant", "text"],
                             partition_names=partition_names)
        return sorted({h["entity"]["tenant"] for h in hits[0]})

    res["scoped_partition_a"] = search(partition_names=["tenant-a"])
    res["all_partitions"] = search()
    return res


def suite_partition_key(client, name: str) -> dict:
    """Partition Key：指定字段做分片键，检索必须带该键过滤。"""
    from pymilvus import DataType

    res: dict = {"collection": name}
    if client.has_collection(name):
        client.drop_collection(name)
    schema = client.create_schema(auto_id=False, enable_dynamic_field=True)
    schema.add_field("id", DataType.INT64, is_primary=True)
    schema.add_field("vector", DataType.FLOAT_VECTOR, dim=DIM)
    schema.add_field("tenant", DataType.VARCHAR, max_length=64, is_partition_key=True)
    schema.add_field("text", DataType.VARCHAR, max_length=256)
    index_params = client.prepare_index_params()
    index_params.add_index(field_name="vector", index_type="AUTOINDEX", metric_type="COSINE")
    client.create_collection(name, schema=schema, index_params=index_params,
                             num_partitions=16)
    client.insert(name, rows())
    client.flush(name)
    res["num_partitions"] = client.describe_collection(name).get("num_partitions")

    def search(flt):
        hits = client.search(name, data=[q("tenant-a")], filter=flt, limit=10,
                             output_fields=["tenant", "text"])
        return sorted({h["entity"]["tenant"] for h in hits[0]})

    try:
        res["with_key_filter"] = search('tenant == "tenant-a"')
    except Exception as exc:  # noqa: BLE001
        res["with_key_filter"] = f"ERROR: {type(exc).__name__}: {exc}"
    try:
        res["without_key_filter"] = search('id >= 0')
    except Exception as exc:  # noqa: BLE001
        res["without_key_filter"] = f"ERROR: {type(exc).__name__}: {exc}"
    return res


def cleanup(client, names) -> None:
    for name in names:
        try:
            if client.has_collection(name):
                client.drop_collection(name)
        except Exception as exc:  # noqa: BLE001
            log(f"cleanup {name}: {exc}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--suite", default="all",
                    choices=["all", "rbac", "filter", "partition", "partition-key"])
    ap.add_argument("--prefix", default="crash25")
    ap.add_argument("--cleanup", action="store_true")
    ap.add_argument("--keep", action="store_true", help="不清理（便于控制面复核）")
    args = ap.parse_args()

    load_env()
    client = make_client()
    names = [f"{args.prefix}_filter", f"{args.prefix}_partition", f"{args.prefix}_pkey"]

    if args.cleanup:
        cleanup(client, names)
        log("cleanup done")
        return 0

    log(f"connected to {os.environ.get('MEMORY_CLOUD_URI')} (secrets redacted)")
    log(f"collections: {client.list_collections()}")

    result: dict = {"uri": os.environ.get("MEMORY_CLOUD_URI")}
    if args.suite in ("all", "rbac"):
        result["rbac"] = suite_rbac(client)
    if args.suite in ("all", "filter"):
        result["filter"] = suite_filter(client, names[0])
    if args.suite in ("all", "partition"):
        result["partition"] = suite_partition(client, names[1])
    if args.suite in ("all", "partition-key"):
        result["partition_key"] = suite_partition_key(client, names[2])

    if not args.keep:
        cleanup(client, names)

    import json

    result = redact(result)
    log(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    out = os.environ.get("SMOKE_OUT")
    if out:
        with open(out, "w", encoding="utf-8") as fh:
            json.dump(result, fh, ensure_ascii=False, indent=2, default=str)
        log(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
