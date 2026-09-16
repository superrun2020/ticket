#!/usr/bin/env python3
"""Configure SPF, DKIM and DMARC for migrated Cloudflare/GoDaddy domains."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import ssl
import urllib.parse
import urllib.request
from pathlib import Path

CF_BASE = "https://api.cloudflare.com/client/v4"
GD_BASE = "https://api.godaddy.com/v1"


def request_json(url: str, headers: dict, method: str = "GET", payload=None):
    body = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    with urllib.request.urlopen(request, context=ssl.create_default_context(), timeout=30) as response:
        content = response.read()
        return json.loads(content.decode() or "null")


def stalwart_domains(base_url: str, token: str) -> dict[str, dict]:
    calls = [
        ["x:Domain/query", {}, "queryDomains"],
        [
            "x:Domain/get",
            {"#ids": {"resultOf": "queryDomains", "name": "x:Domain/query", "path": "/ids"}},
            "getDomains",
        ],
    ]
    payload = request_json(
        f"{base_url.rstrip('/')}/jmap/",
        {"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        "POST",
        {"using": ["urn:ietf:params:jmap:core", "urn:stalwart:jmap"], "methodCalls": calls},
    )
    result = next(item[1] for item in payload["methodResponses"] if item[2] == "getDomains")
    return {row["name"].lower(): row for row in result.get("list", [])}


def mail_auth_records(domain: str, zone: str) -> dict[str, list[dict]]:
    logical, pending = [], ""
    for raw_line in zone.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        pending = f"{pending} {line}".strip()
        if "(" in pending and ")" not in pending:
            continue
        logical.append(pending)
        pending = ""
    records = {"spf": [], "dkim": [], "dmarc": []}
    for line in logical:
        match = re.match(r"^(\S+)\s+IN\s+TXT\s+(.+)$", line, re.I)
        if not match:
            continue
        fqdn, raw_value = match.groups()
        parts = re.findall(r'"([^"]*)"', raw_value)
        value = "".join(parts) if parts else raw_value.strip().strip("()").strip()
        fqdn = fqdn.rstrip(".").lower()
        name = "@" if fqdn == domain else fqdn[: -(len(domain) + 1)]
        record = {"name": name, "fqdn": domain if name == "@" else f"{name}.{domain}", "value": value}
        if value.lower().startswith("v=spf1"):
            records["spf"].append(record)
        elif value.lower().startswith("v=dkim1"):
            records["dkim"].append(record)
        elif value.lower().startswith("v=dmarc1"):
            records["dmarc"].append(record)
    return records


def merged_spf(existing: str | None, expected: str) -> str:
    if not existing or not existing.lower().startswith("v=spf1"):
        return expected
    tokens = existing.split()
    mechanisms = [
        token
        for token in tokens[1:]
        if token.lower() != "include:mail.willech.com" and not re.match(r"^[+?~-]?all$", token, re.I)
    ]
    if not any(token.lstrip("+?~-").lower().split(":", 1)[0].split("/", 1)[0] == "mx" for token in mechanisms):
        mechanisms.append("mx")
    terminal = next((token for token in reversed(tokens[1:]) if re.match(r"^[+?~-]?all$", token, re.I)), "-all")
    return " ".join(["v=spf1", *mechanisms, terminal])


class Cloudflare:
    def __init__(self, token: str):
        self.headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        payload = request_json(f"{CF_BASE}/zones?per_page=50", self.headers)
        if not payload.get("success"):
            raise RuntimeError(str(payload.get("errors")))
        self.zones = {row["name"].lower(): row["id"] for row in payload["result"]}

    def records(self, domain: str, fqdn: str) -> list[dict]:
        zone_id = self.zones[domain]
        query = urllib.parse.urlencode({"type": "TXT", "name": fqdn, "per_page": 100})
        return request_json(f"{CF_BASE}/zones/{zone_id}/dns_records?{query}", self.headers)["result"]

    def upsert(self, domain: str, fqdn: str, value: str, current: dict | None):
        zone_id = self.zones[domain]
        payload = {"type": "TXT", "name": fqdn, "content": value, "ttl": 600}
        if current:
            request_json(f"{CF_BASE}/zones/{zone_id}/dns_records/{current['id']}", self.headers, "PUT", payload)
        else:
            request_json(f"{CF_BASE}/zones/{zone_id}/dns_records", self.headers, "POST", payload)


class GoDaddy:
    def __init__(self, token: str):
        self.headers = {"Authorization": f"Bearer {token}", "Accept": "application/json", "Content-Type": "application/json"}

    def records(self, domain: str, name: str) -> list[dict]:
        return request_json(f"{GD_BASE}/domains/{domain}/records/TXT/{urllib.parse.quote(name)}", self.headers)

    def replace(self, domain: str, name: str, records: list[dict]):
        request_json(
            f"{GD_BASE}/domains/{domain}/records/TXT/{urllib.parse.quote(name)}",
            self.headers,
            "PUT",
            records,
        )


def record_backup(backup: list[dict], backup_path: Path, entry: dict) -> None:
    """Persist rollback data before the corresponding DNS mutation."""
    backup.append(entry)
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = backup_path.with_suffix(f"{backup_path.suffix}.tmp")
    temporary.write_text(
        json.dumps({"created_at": dt.datetime.now(dt.timezone.utc).isoformat(), "records": backup}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(backup_path)


def configure_cloudflare(
    provider: Cloudflare, domain: str, expected: dict, backup: list[dict], backup_path: Path
) -> int:
    changes = 0
    spf = expected["spf"][0]
    rows = provider.records(domain, spf["fqdn"])
    current = next((row for row in rows if str(row.get("content", "")).lower().startswith("v=spf1")), None)
    desired = merged_spf(current.get("content") if current else None, spf["value"])
    record_backup(backup, backup_path, {"provider": "cloudflare", "domain": domain, "name": spf["name"], "before": rows})
    if not current or current.get("content") != desired:
        provider.upsert(domain, spf["fqdn"], desired, current)
        changes += 1
    for record in expected["dkim"]:
        rows = provider.records(domain, record["fqdn"])
        current = next((row for row in rows if str(row.get("content", "")).lower().startswith("v=dkim1")), None)
        record_backup(
            backup, backup_path, {"provider": "cloudflare", "domain": domain, "name": record["name"], "before": rows}
        )
        if not current or current.get("content") != record["value"]:
            provider.upsert(domain, record["fqdn"], record["value"], current)
            changes += 1
    dmarc = expected["dmarc"][0]
    rows = provider.records(domain, dmarc["fqdn"])
    current = next((row for row in rows if str(row.get("content", "")).lower().startswith("v=dmarc1")), None)
    record_backup(backup, backup_path, {"provider": "cloudflare", "domain": domain, "name": dmarc["name"], "before": rows})
    if not current:
        provider.upsert(domain, dmarc["fqdn"], dmarc["value"], None)
        changes += 1
    return changes


def configure_godaddy(
    provider: GoDaddy, domain: str, expected: dict, backup: list[dict], backup_path: Path
) -> int:
    changes = 0
    for kind in ("spf", "dkim", "dmarc"):
        for record in expected[kind]:
            rows = provider.records(domain, record["name"])
            record_backup(
                backup, backup_path, {"provider": "godaddy", "domain": domain, "name": record["name"], "before": rows}
            )
            prefix = f"v={kind}1" if kind != "spf" else "v=spf1"
            index = next((i for i, row in enumerate(rows) if str(row.get("data", "")).lower().startswith(prefix)), None)
            if kind == "spf":
                desired = merged_spf(rows[index]["data"] if index is not None else None, record["value"])
            elif kind == "dmarc" and index is not None:
                continue
            else:
                desired = record["value"]
            if index is not None and rows[index].get("data") == desired:
                continue
            new_row = {"data": desired, "ttl": 600}
            if index is None:
                rows.append(new_row)
            else:
                rows[index] = new_row
            provider.replace(domain, record["name"], rows)
            changes += 1
    return changes


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cloudflare-domains", nargs="*", default=[])
    parser.add_argument("--godaddy-domains", nargs="*", default=[])
    parser.add_argument("--backup", type=Path, required=True)
    args = parser.parse_args()
    stalwart = stalwart_domains(os.getenv("STALWART_API_BASE_URL", "https://mail2.willech.com"), os.environ["STALWART_API_KEY"])
    cloudflare = Cloudflare(os.environ["CF_API_TOKEN"]) if args.cloudflare_domains else None
    godaddy = GoDaddy(os.environ["GODADDY_API_TOKEN"]) if args.godaddy_domains else None
    backup, total_changes = [], 0
    for provider_name, domains in (("cloudflare", args.cloudflare_domains), ("godaddy", args.godaddy_domains)):
        for domain in domains:
            domain = domain.lower()
            if domain not in stalwart:
                raise RuntimeError(f"Stalwart domain missing: {domain}")
            expected = mail_auth_records(domain, stalwart[domain].get("dnsZoneFile", ""))
            if len(expected["spf"]) != 1 or len(expected["dkim"]) < 1 or len(expected["dmarc"]) != 1:
                raise RuntimeError(f"Incomplete Stalwart DNS records: {domain}")
            if provider_name == "cloudflare":
                total_changes += configure_cloudflare(cloudflare, domain, expected, backup, args.backup)
            else:
                total_changes += configure_godaddy(godaddy, domain, expected, backup, args.backup)
            print(f"{provider_name} {domain} complete", flush=True)
    if not args.backup.exists():
        record_backup(backup, args.backup, {"note": "No DNS records required processing"})
    print(f"SUMMARY domains={len(args.cloudflare_domains) + len(args.godaddy_domains)} changes={total_changes} backup={args.backup}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
