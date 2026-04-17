"""Import AI-First Migration template tables from CSV using Azure AD identity.

This script is the Python replacement for `Import-MigrationAgentTables.ps1`. It takes the
CSV files produced by `export_migration_agent_tables.py` (or the PowerShell export script)
and restores their contents into Azure Table Storage using the caller's Azure identity.

Example usage:

```
python import_migration_agent_tables.py \
    --storage-account mystorageaccount \
    --resource-group my-resource-group \
    --input-dir ./template_tables \
    --table-prefix dev \
    --overwrite
```

Prerequisites:
    * `az login` (identity needs `Storage Table Data Contributor` or higher)
    * CSV exports with `PartitionKey` and `RowKey` columns
    * Dependencies from `requirements.txt` (`azure-identity`, `azure-data-tables`)
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
from typing import Any, Dict, Iterable, List, Sequence, Tuple

from azure.core.exceptions import HttpResponseError, ResourceExistsError
from azure.data.tables import TableClient, TableServiceClient
from azure.identity import DefaultAzureCredential


DEFAULT_TEMPLATE_TABLES: Sequence[str] = (
    "AppDetailsTemplate",
    "IntegrationDependencyTemplate",
    "MsSqlDBTemplate",
    "OracleDBTemplate",
    "InfrastructureDetails",
    "K8Stemplate"
)

DEFAULT_INPUT_DIR = Path("TableExports")


@dataclass
class ImportResult:
    table: str
    status: str
    processed: int = 0
    skipped: int = 0
    errors: int = 0
    total_rows: int = 0
    error_message: str | None = None

    def as_manifest_entry(self) -> Dict[str, Any]:
        data = {
            "table": self.table,
            "status": self.status,
            "processed": self.processed,
            "skipped": self.skipped,
            "errors": self.errors,
            "totalRows": self.total_rows,
        }
        if self.error_message:
            data["error"] = self.error_message
        return data


def configure_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format="[%(levelname)s] %(message)s")


def build_table_service_client(account_name: str) -> TableServiceClient:
    if not account_name:
        raise ValueError("Storage account name must be provided")

    credential = DefaultAzureCredential(exclude_interactive_browser_credential=False)
    credential.get_token("https://storage.azure.com/.default")

    account_url = f"https://{account_name}.table.core.windows.net"
    logging.debug("Connecting to table endpoint %s", account_url)
    return TableServiceClient(endpoint=account_url, credential=credential)


def ensure_input_directory(path: Path) -> Path:
    if not path.exists():
        raise FileNotFoundError(f"Input directory '{path}' does not exist")
    if not path.is_dir():
        raise NotADirectoryError(f"Input path '{path}' is not a directory")
    return path.resolve()


def parse_csv(file_path: Path) -> Tuple[List[Dict[str, str]], List[str]]:
    with file_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = [row for row in reader]
        headers = reader.fieldnames or []
    return rows, headers


def detect_duplicates(rows: Iterable[Dict[str, str]]) -> int:
    seen = set()
    duplicates = 0
    for row in rows:
        key = (row.get("PartitionKey"), row.get("RowKey"))
        if key in seen:
            duplicates += 1
        else:
            seen.add(key)
    return duplicates


def is_truthy(value: str) -> bool:
    return value.lower() in {"true", "1", "yes", "y"}


def try_parse_datetime(value: str) -> datetime | None:
    try:
        # Support Z suffix and offset formats
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def convert_property_value(value: str) -> Any:
    if value is None:
        return None
    value = value.strip()
    if value == "":
        return None

    lower = value.lower()
    if lower in {"true", "false"}:
        return is_truthy(value)

    dt = try_parse_datetime(value)
    if dt:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt

    if value.isdigit():
        try:
            return int(value)
        except ValueError:
            pass

    try:
        if value.count(".") == 1 and all(part.isdigit() for part in value.split(".")):
            return float(value)
    except ValueError:
        pass

    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return value


def validate_csv(rows: List[Dict[str, str]], headers: List[str], table_name: str) -> Dict[str, Any]:
    if not headers:
        return {"valid": False, "error": "CSV file has no headers"}

    missing = {"PartitionKey", "RowKey"} - set(headers)
    if missing:
        return {
            "valid": False,
            "error": f"Missing required columns: {', '.join(sorted(missing))}",
        }

    duplicates = detect_duplicates(rows)
    if duplicates:
        logging.warning("Found %d duplicate key combinations in %s", duplicates, table_name)

    return {
        "valid": True,
        "row_count": len(rows),
        "header_count": len(headers),
        "duplicates": duplicates,
    }


def ensure_table(service_client: TableServiceClient, table_name: str) -> TableClient:
    try:
        service_client.create_table(table_name=table_name)
        logging.info("Created table %s", table_name)
    except ResourceExistsError:
        logging.debug("Table %s already exists", table_name)
    except HttpResponseError as exc:
        # Some RBAC configurations prevent table creation checks; try to get client anyway.
        logging.warning("Could not create table %s (%s). Continuing.", table_name, exc)

    return service_client.get_table_client(table_name)


def entity_from_row(row: Dict[str, str]) -> Dict[str, Any]:
    entity: Dict[str, Any] = {
        "PartitionKey": row["PartitionKey"],
        "RowKey": row["RowKey"],
    }

    for key, value in row.items():
        if key in {"PartitionKey", "RowKey", "Timestamp", "ETag"}:
            continue
        converted = convert_property_value(value)
        if converted is not None:
            entity[key] = converted

    return entity


def import_table(
    service_client: TableServiceClient,
    logical_table_name: str,
    csv_path: Path,
    prefix: str,
    overwrite: bool,
    validate_only: bool,
) -> ImportResult:
    physical_name = f"{prefix}{logical_table_name}" if prefix else logical_table_name
    logging.info("Processing %s", csv_path.name)

    try:
        rows, headers = parse_csv(csv_path)
    except Exception as exc:  # noqa: BLE001
        logging.error("Failed to read %s: %s", csv_path, exc)
        return ImportResult(physical_name, "Error", error_message=str(exc))

    validation = validate_csv(rows, headers, physical_name)
    if not validation.get("valid", False):
        logging.error("CSV validation failed for %s: %s", physical_name, validation["error"])
        return ImportResult(physical_name, "ValidationFailed", error_message=validation["error"])

    logging.info(
        "Validation passed for %s: %d rows, %d columns", 
        physical_name,
        validation["row_count"],
        validation["header_count"],
    )

    if validate_only:
        return ImportResult(
            table=physical_name,
            status="ValidatedOnly",
            processed=0,
            skipped=0,
            errors=0,
            total_rows=validation["row_count"],
        )

    table_client = ensure_table(service_client, physical_name)

    processed = 0
    skipped = 0
    errors = 0

    for index, row in enumerate(rows, start=1):
        if index % 50 == 0:
            logging.debug("Progress %s: %d/%d", physical_name, index, len(rows))

        try:
            entity = entity_from_row(row)
            if overwrite:
                table_client.upsert_entity(entity=entity, mode="replace")
            else:
                table_client.create_entity(entity=entity)
            processed += 1
        except ResourceExistsError:
            skipped += 1
        except HttpResponseError as exc:
            errors += 1
            if errors <= 5:
                logging.warning(
                    "Failed to import entity PK=%s RK=%s into %s: %s",
                    row.get("PartitionKey"),
                    row.get("RowKey"),
                    physical_name,
                    exc,
                )

    status = "Success" if errors == 0 else "Partial"
    return ImportResult(
        table=physical_name,
        status=status,
        processed=processed,
        skipped=skipped,
        errors=errors,
        total_rows=len(rows),
    )


def discover_table_files(input_dir: Path, expected_tables: Sequence[str]) -> Dict[str, Path]:
    found: Dict[str, Path] = {}
    for table in expected_tables:
        candidate = input_dir / f"{table}.csv"
        if candidate.exists():
            found[table] = candidate
            logging.info("Found CSV for %s", table)
        else:
            logging.warning("Missing CSV for %s", table)
    return found


def collect_additional_files(input_dir: Path, expected_tables: Sequence[str]) -> List[Path]:
    additional: List[Path] = []
    for csv_file in sorted(input_dir.glob("*.csv")):
        if csv_file.stem not in expected_tables:
            additional.append(csv_file)
    return additional


def write_manifest(
    input_dir: Path,
    account_name: str,
    resource_group: str | None,
    prefix: str,
    overwrite: bool,
    results: Sequence[ImportResult],
) -> Path:
    manifest = {
        "importDate": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "storageAccount": account_name,
        "resourceGroup": resource_group,
        "tablePrefix": prefix or None,
        "inputDirectory": str(input_dir),
        "overwriteExisting": overwrite,
        "summary": {
            "totalTables": len(results),
            "successfulImports": sum(1 for r in results if r.status == "Success"),
            "processedEntities": sum(r.processed for r in results),
        },
        "results": [result.as_manifest_entry() for result in results],
    }

    manifest_path = input_dir / "import-manifest.json"
    with manifest_path.open("w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2)

    logging.info("Import manifest written to %s", manifest_path)
    return manifest_path


def evaluate_exit_code(results: Sequence[ImportResult]) -> int:
    if any(r.status == "Error" for r in results):
        return 1
    if any(r.status == "Partial" for r in results):
        return 1
    return 0


def parse_arguments(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Import AI-First Migration template tables from CSV files",
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
        "--input-dir",
        type=Path,
        default=DEFAULT_INPUT_DIR,
        help="Directory containing the CSV files to import (default: %(default)s)",
    )
    parser.add_argument(
        "--table-prefix",
        default="",
        help="Optional prefix applied to each table name when importing.",
    )
    parser.add_argument(
        "--tables",
        nargs="*",
        default=DEFAULT_TEMPLATE_TABLES,
        help="Override the default list of template tables.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing entities instead of skipping duplicates.",
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Validate CSV files without importing data.",
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

    logging.info("Starting table import")
    logging.info("Target tables: %s", ", ".join(args.tables))
    if args.resource_group:
        logging.info("Resource group: %s", args.resource_group)
    logging.info("Overwrite existing: %s", args.overwrite)
    logging.info("Validate only: %s", args.validate_only)

    try:
        input_dir = ensure_input_directory(args.input_dir)
    except (FileNotFoundError, NotADirectoryError) as exc:
        logging.error("%s", exc)
        return 1

    found_tables = discover_table_files(input_dir, args.tables)
    additional_files = collect_additional_files(input_dir, args.tables)

    if additional_files:
        logging.info(
            "Additional CSV files detected and skipped: %s",
            ", ".join(file.name for file in additional_files),
        )

    if not found_tables:
        logging.error("No expected template CSV files were found in %s", input_dir)
        return 1

    try:
        service_client = build_table_service_client(args.storage_account)
    except Exception as exc:  # noqa: BLE001
        logging.error("Failed to create table service client: %s", exc)
        return 1

    results: List[ImportResult] = []
    for table_name, csv_path in found_tables.items():
        result = import_table(
            service_client=service_client,
            logical_table_name=table_name,
            csv_path=csv_path,
            prefix=args.table_prefix,
            overwrite=args.overwrite,
            validate_only=args.validate_only,
        )
        results.append(result)

    if not args.validate_only:
        manifest_path = write_manifest(
            input_dir=input_dir,
            account_name=args.storage_account,
            resource_group=args.resource_group,
            prefix=args.table_prefix,
            overwrite=args.overwrite,
            results=results,
        )
        logging.info("Import completed. Manifest: %s", manifest_path)
    else:
        logging.info("Validation completed (no data imported).")

    success = sum(1 for r in results if r.status == "Success")
    logging.info(
        "Successfully processed %d/%d tables", success, len(results)
    )

    total_processed = sum(r.processed for r in results)
    total_skipped = sum(r.skipped for r in results)
    total_errors = sum(r.errors for r in results)
    logging.info(
        "Summary: processed=%d skipped=%d errors=%d", 
        total_processed,
        total_skipped,
        total_errors,
    )

    return evaluate_exit_code(results)


if __name__ == "__main__":
    sys.exit(main())
