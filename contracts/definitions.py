"""Shared wire contract owned by A-02, independent of server/Pydantic implementation.

Export with tools/export_contracts.py. Business validation (references, arithmetic,
version conflicts) belongs to C/N services and cannot be established by JSON Schema.
"""


def ref(name):
    return {"$ref": f"#/components/schemas/{name}"}


def nullable(schema):
    return {"anyOf": [schema, {"type": "null"}]}


def array(schema, **constraints):
    return {"type": "array", "items": schema, **constraints}


def obj(properties, required=None, **constraints):
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties) if required is None else required,
        "additionalProperties": False,
        **constraints,
    }


STRING = {"type": "string", "minLength": 1, "pattern": r"\S"}
BOOL = {"type": "boolean"}
NUMBER = {"type": "number", "minimum": 0}
INTEGER = {"type": "integer", "minimum": 0}
UUID = {"type": "string", "format": "uuid"}
TIME = {"type": "string", "format": "date-time"}
SCORE = {"type": "number", "minimum": 0, "maximum": 100}
ANSWER = {"type": ["boolean", "number", "null"], "minimum": 0}
APPLICABILITY = {"enum": ["applicable", "not_applicable"]}
STATUS = {"enum": ["queued", "running", "completed", "failed"]}
LANGUAGE = {"enum": ["RU", "KZ", "EN"]}
CHANNEL = {"enum": ["agent chat", "voice call"]}
SPEAKER = {"enum": ["client", "operator", "bot", "system"]}
MESSAGE_ID = {"type": "string", "pattern": r"^m[0-9]{4,}$"}

SCHEMAS = {
    "Health": obj({"status": {"const": "ok"}}),
    "ErrorDetail": obj({"loc": array({"type": ["string", "integer"]}), "type": STRING}),
    "ErrorBody": obj(
        {"code": STRING, "message": STRING, "details": array(ref("ErrorDetail"))}
    ),
    "Error": obj({"error": ref("ErrorBody")}),
    "OrganizerMessage": obj({"timestamp": STRING, "speaker": SPEAKER, "text": STRING}),
    "OrganizerRecord": obj(
        {
            "conversation_id": STRING,
            "language": LANGUAGE,
            "channel": CHANNEL,
            "transcript": array(ref("OrganizerMessage"), minItems=1),
            "audio_url": nullable({"type": "string", "format": "uri"}),
        }
    ),
    "DatasetImportRequest": obj({"group_id": STRING}),
    "ConversationImportRequest": obj(
        {"records": array(ref("OrganizerRecord"), minItems=1)}
    ),
    "ImportSummary": obj(
        {
            "group_id": STRING,
            "received": INTEGER,
            "created": INTEGER,
            "new_versions": INTEGER,
            "unchanged": INTEGER,
            "conversation_ids": array(UUID, uniqueItems=True),
        }
    ),
    "Message": obj(
        {
            "id": MESSAGE_ID,
            "position": {"type": "integer", "minimum": 1},
            "timestamp": STRING,
            "speaker": SPEAKER,
            "text": STRING,
        }
    ),
    "LatestEvaluation": obj(
        {
            "id": UUID,
            "status": STATUS,
            "original_total": nullable(SCORE),
            "final_total": nullable(SCORE),
            "created_at": TIME,
        }
    ),
    "ScaleStep": obj({"value": NUMBER, "description": STRING}),
    "Criterion": obj(
        {
            "id": STRING,
            "name": STRING,
            "instruction": STRING,
            "type": {"enum": ["binary", "scale", "flag"]},
            "weight": NUMBER,
            "allow_na": BOOL,
            "na_rule": nullable(STRING),
            "scale": nullable(array(ref("ScaleStep"), minItems=2)),
            "section": nullable(STRING),
        },
        allOf=[
            {
                "if": {"properties": {"type": {"const": "flag"}}},
                "then": {
                    "properties": {
                        "weight": {"const": 0},
                        "scale": {"type": "null"},
                        "allow_na": {"const": False},
                        "na_rule": {"type": "null"},
                    }
                },
                "else": {"properties": {"weight": {"exclusiveMinimum": 0}}},
            },
            {
                "if": {"properties": {"type": {"const": "binary"}}},
                "then": {"properties": {"scale": {"type": "null"}}},
            },
            {
                "if": {"properties": {"type": {"const": "scale"}}},
                "then": {
                    "properties": {
                        "scale": {
                            "type": "array",
                            "minItems": 2,
                            "contains": {
                                "properties": {"value": {"exclusiveMinimum": 0}}
                            },
                        }
                    }
                },
            },
            {
                "if": {"properties": {"allow_na": {"const": True}}},
                "then": {"properties": {"na_rule": STRING}},
                "else": {"properties": {"na_rule": {"type": "null"}}},
            },
        ],
    ),
    "MessageEvidence": obj({"kind": {"const": "message"}, "message_id": MESSAGE_ID}),
    "RangeEvidence": obj(
        {
            "kind": {"const": "range"},
            "start_message_id": MESSAGE_ID,
            "end_message_id": MESSAGE_ID,
        }
    ),
    "Evidence": {"oneOf": [ref("MessageEvidence"), ref("RangeEvidence")]},
}

CRITERIA = array(
    ref("Criterion"),
    minItems=1,
    contains={"properties": {"type": {"enum": ["binary", "scale"]}}},
)
SCHEMAS.update(
    {
        "ChecklistCreate": obj({"name": STRING, "criteria": CRITERIA}),
        "ChecklistVersionCreate": obj(
            {
                "expected_version_number": {"type": "integer", "minimum": 1},
                "criteria": CRITERIA,
            }
        ),
        "ChecklistVersion": obj(
            {
                "id": UUID,
                "checklist_id": UUID,
                "name": STRING,
                "version_number": {"type": "integer", "minimum": 1},
                "created_at": TIME,
                "criteria": CRITERIA,
            }
        ),
        "ChecklistSummary": obj(
            {
                "id": UUID,
                "name": STRING,
                "latest_version_id": UUID,
                "latest_version_number": {"type": "integer", "minimum": 1},
            }
        ),
        "ChecklistDetail": obj(
            {
                "id": UUID,
                "name": STRING,
                "current_version": ref("ChecklistVersion"),
                "versions": array(ref("ChecklistVersion"), minItems=1),
            }
        ),
        "TranscriptSnapshot": obj(
            {
                "id": UUID,
                "conversation_id": UUID,
                "language": LANGUAGE,
                "channel": CHANNEL,
                "messages": array(ref("Message"), minItems=1),
            }
        ),
        "Usage": obj(
            {
                "request_count": INTEGER,
                "input_tokens": nullable(INTEGER),
                "output_tokens": nullable(INTEGER),
                "total_tokens": nullable(INTEGER),
                "cached_input_tokens": nullable(INTEGER),
                "reasoning_output_tokens": nullable(INTEGER),
            }
        ),
        "ProviderCriterionResult": obj(
            {
                "criterion_id": STRING,
                "applicability": APPLICABILITY,
                "answer": ANSWER,
                "explanation": STRING,
                "evidence": array(ref("Evidence"), minItems=1),
                "review_reasons": array(STRING),
            },
            allOf=[
                {
                    "if": {
                        "properties": {"applicability": {"const": "not_applicable"}}
                    },
                    "then": {"properties": {"answer": {"type": "null"}}},
                    "else": {"properties": {"answer": {"type": ["boolean", "number"]}}},
                },
            ],
        ),
        "ProviderResult": obj(
            {
                "criterion_results": array(ref("ProviderCriterionResult"), minItems=1),
                "provider": STRING,
                "model_requested": STRING,
                "model_reported": nullable(STRING),
                "usage": ref("Usage"),
                "duration_ms": INTEGER,
                "review_reasons": array(STRING),
            }
        ),
        "Provenance": obj(
            {
                "provider": STRING,
                "model_requested": STRING,
                "model_reported": nullable(STRING),
                "prompt_version": STRING,
                "duration_ms": INTEGER,
                "usage": ref("Usage"),
                "estimated_cost_usd": nullable(NUMBER),
            }
        ),
        "SectionAssessment": obj(
            {
                "name": nullable(STRING),
                "base_points": NUMBER,
                "applicable_weight": NUMBER,
                "total": nullable(SCORE),
            }
        ),
    }
)

SCHEMAS["CriterionAssessment"] = obj(
    {
        **SCHEMAS["ProviderCriterionResult"]["properties"],
        "weight": NUMBER,
        "base_points": NUMBER,
        "effective_weight": NUMBER,
        "effective_points": NUMBER,
        "review_required": BOOL,
    },
    allOf=SCHEMAS["ProviderCriterionResult"]["allOf"],
)
SCHEMAS["Assessment"] = obj(
    {
        "total": nullable(SCORE),
        "no_applicable_reason": nullable(STRING),
        "gross_error": nullable(BOOL),
        "criteria": array(ref("CriterionAssessment"), minItems=1),
        "sections": array(ref("SectionAssessment")),
    }
)
SCHEMAS["EvaluationCreate"] = obj(
    {"conversation_id": UUID, "checklist_version_id": UUID}
)
SCHEMAS["Evaluation"] = obj(
    {
        "id": UUID,
        "conversation_id": UUID,
        "transcript_version_id": UUID,
        "checklist_version_id": UUID,
        "status": STATUS,
        "revision": INTEGER,
        "created_at": TIME,
        "completed_at": nullable(TIME),
        "original": nullable(ref("Assessment")),
        "final": nullable(ref("Assessment")),
        "review_required": BOOL,
        "review_reasons": array(STRING),
        "provenance": nullable(ref("Provenance")),
        "error": nullable(ref("ErrorBody")),
    },
    allOf=[
        {
            "if": {"properties": {"status": {"const": "completed"}}},
            "then": {
                "properties": {
                    "original": ref("Assessment"),
                    "final": ref("Assessment"),
                    "provenance": ref("Provenance"),
                    "completed_at": TIME,
                    "error": {"type": "null"},
                }
            },
            "else": {
                "properties": {
                    "original": {"type": "null"},
                    "final": {"type": "null"},
                    "revision": {"const": 0},
                }
            },
        },
        {
            "if": {"properties": {"status": {"const": "failed"}}},
            "then": {"properties": {"error": ref("ErrorBody"), "completed_at": TIME}},
            "else": {"properties": {"error": {"type": "null"}}},
        },
        {
            "if": {"properties": {"status": {"enum": ["queued", "running"]}}},
            "then": {"properties": {"completed_at": {"type": "null"}}},
        },
    ],
)

summary = {
    "id": UUID,
    "external_id": STRING,
    "source": {"enum": ["dataset", "upload"]},
    "group_id": STRING,
    "language": LANGUAGE,
    "channel": CHANNEL,
    "current_transcript_version_id": UUID,
    "message_count": {"type": "integer", "minimum": 1},
    "latest_evaluation": nullable(ref("LatestEvaluation")),
}
SCHEMAS["ConversationSummary"] = obj(summary)
SCHEMAS["ConversationDetail"] = obj(
    {
        **summary,
        "transcript_version_id": UUID,
        "audio_url": nullable({"type": "string", "format": "uri"}),
        "messages": array(ref("Message"), minItems=1),
    }
)
SCHEMAS["AppealCreate"] = obj({"criterion_id": STRING, "comment": STRING})
SCHEMAS["ConfirmDecision"] = obj(
    {"action": {"const": "confirm"}, "reason": STRING, "expected_revision": INTEGER}
)
SCHEMAS["OverrideDecision"] = obj(
    {
        "action": {"const": "override"},
        "reason": STRING,
        "expected_revision": INTEGER,
        "applicability": APPLICABILITY,
        "answer": ANSWER,
        "evidence": array(ref("Evidence"), minItems=1),
    },
    allOf=SCHEMAS["ProviderCriterionResult"]["allOf"],
)
SCHEMAS["DecisionCreate"] = {"oneOf": [ref("ConfirmDecision"), ref("OverrideDecision")]}
SCHEMAS["AppealDecision"] = obj(
    {
        "id": UUID,
        "appeal_id": UUID,
        "action": {"enum": ["confirm", "override"]},
        "reason": STRING,
        "expected_revision": INTEGER,
        "created_at": TIME,
        "result": ref("ProviderCriterionResult"),
    }
)
SCHEMAS["Appeal"] = obj(
    {
        "id": UUID,
        "evaluation_id": UUID,
        "criterion_id": STRING,
        "comment": STRING,
        "status": {"enum": ["open", "resolved"]},
        "base_revision": INTEGER,
        "current_revision": INTEGER,
        "created_at": TIME,
        "decision": nullable(ref("AppealDecision")),
        "original": ref("CriterionAssessment"),
        "current": ref("CriterionAssessment"),
    },
    allOf=[
        {
            "if": {"properties": {"status": {"const": "open"}}},
            "then": {"properties": {"decision": {"type": "null"}}},
            "else": {"properties": {"decision": ref("AppealDecision")}},
        },
    ],
)
SCHEMAS["DecisionResponse"] = obj(
    {"appeal": ref("Appeal"), "evaluation": ref("Evaluation")}
)

for item in ["ConversationSummary", "ChecklistSummary", "Evaluation", "Appeal"]:
    SCHEMAS[f"{item}Page"] = obj(
        {
            "items": array(ref(item)),
            "total": INTEGER,
            "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            "offset": INTEGER,
        }
    )


def response(schema, description):
    return {
        "description": description,
        "content": {"application/json": {"schema": ref(schema)}},
    }


def parameter(name, schema, location="query", required=False):
    return {"name": name, "in": location, "required": required, "schema": schema}


def operation(operation_id, result, status=200, body=None, parameters=None, errors=()):
    result = {
        "operationId": operation_id,
        "responses": {
            str(status): response(result, "Success"),
            **{str(code): response("Error", "Safe error") for code in errors},
        },
    }
    if body:
        result["requestBody"] = {
            "required": True,
            "content": {"application/json": {"schema": ref(body)}},
        }
    if parameters:
        result["parameters"] = parameters
    return result


PAGINATION = [
    parameter(
        "limit", {"type": "integer", "minimum": 1, "maximum": 100, "default": 50}
    ),
    parameter("offset", {"type": "integer", "minimum": 0, "default": 0}),
]
ID = [parameter("id", UUID, "path", True)]
PATHS = {
    "/health": {"get": operation("health", "Health", errors=(503,))},
    "/api/v1/datasets/import": {
        "post": operation(
            "importDataset",
            "ImportSummary",
            body="DatasetImportRequest",
            errors=(422, 502, 503),
        )
    },
    "/api/v1/conversations/import": {
        "post": operation(
            "importConversations",
            "ImportSummary",
            body="ConversationImportRequest",
            errors=(422,),
        )
    },
    "/api/v1/conversations": {
        "get": operation(
            "listConversations",
            "ConversationSummaryPage",
            parameters=[
                parameter("language", LANGUAGE),
                parameter("channel", CHANNEL),
                parameter("group_id", STRING),
                *PAGINATION,
            ],
            errors=(422,),
        )
    },
    "/api/v1/conversations/{id}": {
        "get": operation(
            "getConversation",
            "ConversationDetail",
            parameters=[*ID, parameter("transcript_version_id", UUID)],
            errors=(404, 422),
        )
    },
    "/api/v1/checklists": {
        "get": operation(
            "listChecklists",
            "ChecklistSummaryPage",
            parameters=PAGINATION,
            errors=(422,),
        ),
        "post": operation(
            "createChecklist", "ChecklistVersion", 201, "ChecklistCreate", errors=(422,)
        ),
    },
    "/api/v1/checklists/{id}": {
        "get": operation(
            "getChecklist", "ChecklistDetail", parameters=ID, errors=(404, 422)
        )
    },
    "/api/v1/checklist-versions/{id}": {
        "get": operation(
            "getChecklistVersion", "ChecklistVersion", parameters=ID, errors=(404, 422)
        )
    },
    "/api/v1/checklists/{id}/versions": {
        "post": operation(
            "createChecklistVersion",
            "ChecklistVersion",
            201,
            "ChecklistVersionCreate",
            ID,
            errors=(404, 409, 422),
        )
    },
    "/api/v1/evaluations": {
        "get": operation(
            "listEvaluations",
            "EvaluationPage",
            parameters=[
                parameter("conversation_id", UUID),
                parameter("status", STATUS),
                *PAGINATION,
            ],
            errors=(422,),
        ),
        "post": operation(
            "createEvaluation",
            "Evaluation",
            202,
            "EvaluationCreate",
            errors=(404, 422, 503),
        ),
    },
    "/api/v1/evaluations/{id}": {
        "get": operation(
            "getEvaluation", "Evaluation", parameters=ID, errors=(404, 422)
        )
    },
    "/api/v1/evaluations/{id}/appeals": {
        "post": operation(
            "createAppeal", "Appeal", 201, "AppealCreate", ID, errors=(404, 409, 422)
        )
    },
    "/api/v1/appeals": {
        "get": operation(
            "listAppeals",
            "AppealPage",
            parameters=[
                parameter("status", {"enum": ["open", "resolved"]}),
                parameter("evaluation_id", UUID),
                *PAGINATION,
            ],
            errors=(422,),
        )
    },
    "/api/v1/appeals/{id}": {
        "get": operation("getAppeal", "Appeal", parameters=ID, errors=(404, 422))
    },
    "/api/v1/appeals/{id}/decisions": {
        "post": operation(
            "decideAppeal",
            "DecisionResponse",
            200,
            "DecisionCreate",
            ID,
            errors=(404, 409, 422),
        )
    },
}

OPENAPI = {
    "openapi": "3.1.0",
    "info": {
        "title": "CQA target API contract",
        "version": "0.1.0",
        "description": "Target contract. Runtime /openapi.json describes implemented routes only.",
    },
    "paths": PATHS,
    "components": {"schemas": SCHEMAS},
}


def provider_schema():
    """Standalone JSON Schema with only the definitions reachable from ProviderResult."""
    definitions = {}

    def rewrite(value):
        if isinstance(value, dict):
            if "$ref" in value:
                name = value["$ref"].rsplit("/", 1)[1]
                if name not in definitions:
                    definitions[name] = {}
                    definitions[name] = rewrite(SCHEMAS[name])
                return {"$ref": f"#/$defs/{name}"}
            return {key: rewrite(item) for key, item in value.items()}
        if isinstance(value, list):
            return [rewrite(item) for item in value]
        return value

    root = rewrite(ref("ProviderResult"))
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        **root,
        "$defs": definitions,
    }
