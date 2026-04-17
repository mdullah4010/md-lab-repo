"""Export AI-First Migration template tables to CSV using Azure AD identity.

This script replaces the PowerShell implementation in `Export-MigrationAgentTables.ps1`
with a Python version that authenticates with the caller's Azure identity. It reads
the template tables used by the migration agents and exports each table to a CSV file,
mirroring the original behaviour while enforcing Azure AD authentication only.

Usage example:

```
python export_migration_agent_tables.py \
    --storage-account mystorageaccount \
    --resource-group my-resource-group \
    --output-dir ./TableExports \
    --table-prefix dev
```

Prerequisites:
        * `az login` (the signed-in identity needs `Storage Table Data Reader` access)
        * `--resource-group` is optional but helps with manifest tracking.
        * Dependencies listed in `requirements.txt` (notably `azure-identity` and
            `azure-data-tables`).
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence

from azure.core.exceptions import HttpResponseError, ResourceNotFoundError
from azure.data.tables import TableClient, TableServiceClient
from azure.identity import DefaultAzureCredential


# -----------------------------
# Constants & configuration
# -----------------------------

DEFAULT_TEMPLATE_TABLES: Sequence[str] = (
    "AppDetailsTemplate",
    "IntegrationDependencyTemplate",
    "MsSqlDBTemplate",
    "OracleDBTemplate",
    "InfrastructureDetails",
)

DEFAULT_OUTPUT_DIR = Path("TableExports")


# -----------------------------
# Data models
# -----------------------------


@dataclass
class ExportResult:
    table: str
    status: str
    entity_count: int = 0
    file_path: Path | None = None
    error: str | None = None

    def as_manifest_entry(self) -> Dict[str, Any]:
        data = {
            "table": self.table,
            "status": self.status,
            "entityCount": self.entity_count,
        }
        if self.file_path is not None:
            data["filePath"] = str(self.file_path)
        if self.error:
            data["error"] = self.error
        return data


# -----------------------------
# Utility helpers
# -----------------------------


def configure_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="[%(levelname)s] %(message)s",
    )


def build_table_service_client(account_name: str) -> TableServiceClient:
    """Create a `TableServiceClient` using the caller's Azure identity."""

    if not account_name:
        raise ValueError("Storage account name must be provided")

    credential = DefaultAzureCredential(exclude_interactive_browser_credential=False)
    # Validate credential early so we fail fast if the identity is not signed in.
    credential.get_token("https://storage.azure.com/.default")

    account_url = f"https://{account_name}.table.core.windows.net"
    logging.debug("Connecting to table endpoint %s", account_url)

    # `azure-data-tables` 12.5 expects the `endpoint` keyword argument.
    # Using keyword form keeps compatibility with newer SDK releases that
    # renamed the first positional parameter from `account_url` to `endpoint`.
    return TableServiceClient(endpoint=account_url, credential=credential)


def ensure_output_directory(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path.resolve()


def normalise_datetime(value: Any) -> str:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return str(value)


def serialise_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return normalise_datetime(value)
    if isinstance(value, (list, dict, tuple)):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def convert_entity(entity: Dict[str, Any]) -> Dict[str, Any]:
    """Convert a `TableEntity` into a flat dictionary ready for CSV output."""

    result: Dict[str, Any] = {
        "PartitionKey": entity.get("PartitionKey", ""),
        "RowKey": entity.get("RowKey", ""),
        "Timestamp": normalise_datetime(entity.get("Timestamp", "")),
        "ETag": getattr(entity, "_metadata", {}).get("etag")
        or getattr(entity, "metadata", {}).get("etag")
        or entity.get("odata.etag", ""),
    }

    for key, value in entity.items():
        if key in ("PartitionKey", "RowKey", "Timestamp"):
            continue
        result[key] = value

    return result


def gather_fieldnames(rows: Iterable[Dict[str, Any]]) -> List[str]:
    base = ["PartitionKey", "RowKey", "Timestamp", "ETag"]
    extras: List[str] = []
    seen = set(base)

    for row in rows:
        for key in row.keys():
            if key not in seen:
                seen.add(key)
                extras.append(key)

    return base + sorted(extras)


def write_csv(file_path: Path, rows: List[Dict[str, Any]]) -> None:
    fieldnames = gather_fieldnames(rows)
    with file_path.open("w", newline="", encoding="utf-8") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: serialise_value(row.get(key)) for key in fieldnames})


def list_available_tables(service_client: TableServiceClient) -> List[str]:
    try:
        return sorted(tbl.name for tbl in service_client.list_tables())
    except HttpResponseError as exc:
        if getattr(exc, "status_code", None) == 403:
            logging.warning(
                "Listing tables returned 403 (likely restricted). We'll continue "
                "because explicit table names were provided. Full error: %s",
                exc,
            )
            return []
        logging.warning("Unable to list tables: %s", exc)
        return []


def fetch_entities(table_client: TableClient) -> List[Dict[str, Any]]:
    entities: List[Dict[str, Any]] = []
    try:
        for entity in table_client.list_entities(results_per_page=1000):
            entities.append(convert_entity(entity))
    except HttpResponseError as exc:
        raise RuntimeError(f"Failed to read entities: {exc}") from exc
    return entities


# -----------------------------
# Export workflow
# -----------------------------


def export_table(
    service_client: TableServiceClient,
    logical_table_name: str,
    prefix: str,
    output_dir: Path,
) -> ExportResult:
    physical_table_name = f"{prefix}{logical_table_name}" if prefix else logical_table_name
    logging.info("Exporting table %s", physical_table_name)

    table_client = service_client.get_table_client(physical_table_name)

    try:
        entities = fetch_entities(table_client)
    except ResourceNotFoundError:
        logging.warning("Table %s not found; skipping", physical_table_name)
        return ExportResult(table=physical_table_name, status="NotFound")
    except RuntimeError as exc:
        logging.error("Failed to export table %s: %s", physical_table_name, exc)
        return ExportResult(
            table=physical_table_name,
            status="Error",
            error=str(exc),
        )

    if not entities:
        logging.warning("Table %s is empty", physical_table_name)
        return ExportResult(table=physical_table_name, status="Empty")

    csv_path = output_dir / f"{physical_table_name}.csv"
    write_csv(csv_path, entities)

    file_size_kb = csv_path.stat().st_size / 1024
    logging.info(
        "Exported %d entities from %s to %s (%.2f KB)",
        len(entities),
        physical_table_name,
        csv_path,
        file_size_kb,
    )

    return ExportResult(
        table=physical_table_name,
        status="Success",
        entity_count=len(entities),
        file_path=csv_path,
    )


def export_tables(
    account_name: str,
    output_dir: Path,
    template_tables: Sequence[str],
    prefix: str,
) -> List[ExportResult]:
    service_client = build_table_service_client(account_name)
    available_tables = list_available_tables(service_client)
    if available_tables:
        logging.info(
            "Found %d tables on storage account %s",
            len(available_tables),
            account_name,
        )
    else:
        logging.debug(
            "Table enumeration skipped or returned no results; relying on provided template list."
        )

    results: List[ExportResult] = []
    for table_name in template_tables:
        result = export_table(service_client, table_name, prefix, output_dir)
        results.append(result)

    return results


def write_manifest(
    output_dir: Path,
    account_name: str,
    resource_group: str | None,
    prefix: str,
    results: Sequence[ExportResult],
) -> Path:
    manifest = {
        "exportDate": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "storageAccount": account_name,
        "resourceGroup": resource_group,
        "tablePrefix": prefix or None,
        "outputDirectory": str(output_dir),
        "summary": {
            "totalTables": len(results),
            "successfulExports": sum(1 for r in results if r.status == "Success"),
            "totalEntities": sum(r.entity_count for r in results),
        },
        "results": [r.as_manifest_entry() for r in results],
    }

    manifest_path = output_dir / "export-manifest.json"
    with manifest_path.open("w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2)

    logging.info("Manifest written to %s", manifest_path)
    return manifest_path


def evaluate_exit_code(results: Sequence[ExportResult]) -> int:
    if any(r.status == "Error" for r in results):
        return 1
    if all(r.status == "NotFound" for r in results):
        return 1
    return 0


# -----------------------------
# CLI entry point
# -----------------------------


def parse_arguments(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export AI-First Migration template tables to CSV files",
    )
    parser.add_argument(
        "--storage-account",
        required=True,
        help="Name of the Azure Storage account containing the tables.",
    )
    parser.add_argument(
        "--resource-group",
        help="Optional Azure Resource Group name (included in the manifest).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Directory where CSV files will be written (default: %(default)s)",
    )
    parser.add_argument(
        "--table-prefix",
        default="",
        help="Optional prefix applied to each logical table name.",
    )
    parser.add_argument(
        "--tables",
        nargs="*",
        default=DEFAULT_TEMPLATE_TABLES,
        help="Override the default list of template tables.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose logging output.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_arguments(argv or sys.argv[1:])
    configure_logging(args.verbose)

    logging.info("Starting table export")
    logging.info("Target tables: %s", ", ".join(args.tables))
    if args.resource_group:
        logging.info("Resource group: %s", args.resource_group)

    output_dir = ensure_output_directory(args.output_dir)

    try:
        results = export_tables(
            account_name=args.storage_account,
            output_dir=output_dir,
            template_tables=args.tables,
            prefix=args.table_prefix,
        )
    except Exception as exc:  # noqa: BLE001
        logging.error("Export failed: %s", exc)
        return 1

    manifest_path = write_manifest(
        output_dir=output_dir,
        account_name=args.storage_account,
        resource_group=args.resource_group,
        prefix=args.table_prefix,
        results=results,
    )

    success_count = sum(1 for r in results if r.status == "Success")
    logging.info(
        "Successfully exported %d/%d tables. Manifest: %s",
        success_count,
        len(results),
        manifest_path,
    )

    return evaluate_exit_code(results)


if __name__ == "__main__":
    sys.exit(main())
