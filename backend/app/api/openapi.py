"""Reuse the shared response contract while keeping FastAPI's live route and input schemas."""

import json
from pathlib import Path

_EXPORT = Path(__file__).resolve().parents[3] / "contracts" / "openapi.json"
_REF = "#/components/schemas/"


def document_wire_responses(app):
    native_openapi = app.openapi

    def openapi():
        if app.openapi_schema is not None:
            return app.openapi_schema
        shared = json.loads(_EXPORT.read_text(encoding="utf-8"))
        wire_schemas = {}

        def wire_refs(value):
            if isinstance(value, list):
                return [wire_refs(item) for item in value]
            if not isinstance(value, dict):
                return value
            result = {}
            for key, item in value.items():
                if key == "$ref" and item.startswith(_REF):
                    name = item.removeprefix(_REF)
                    wire_name = f"Wire{name}"
                    if wire_name not in wire_schemas:
                        wire_schemas[wire_name] = {}
                        wire_schemas[wire_name] = wire_refs(shared["components"]["schemas"][name])
                    result[key] = f"{_REF}{wire_name}"
                else:
                    result[key] = wire_refs(item)
            return result

        schema = native_openapi()
        for path, operations in schema["paths"].items():
            for method, operation in operations.items():
                contract = shared["paths"].get(path, {}).get(method)
                if contract is not None:
                    operation["operationId"] = contract["operationId"]
                    operation["responses"].update(wire_refs(contract["responses"]))
        schema.setdefault("components", {}).setdefault("schemas", {}).update(wire_schemas)
        return schema

    app.openapi = openapi
