import {
  criteriaSchema,
  importSchema,
  type ChecklistVersion,
  type Conversation,
  type OrganizerRecord,
} from "../api/schema";
import type { Transport } from "../api/client";
import { demoChecklist, demoRecords } from "./fixtures";

// State belongs only to this explicitly selected demo session. Reload resets it.
export function createMockTransport(): Transport {
  const conversations: Conversation[] = [];
  const snapshots = new Map<
    string,
    { fingerprint: string; conversation: Conversation }[]
  >();
  const versions: ChecklistVersion[] = [structuredClone(demoChecklist)];
  function importData(
    records: OrganizerRecord[],
    group_id: string,
    source: "dataset" | "upload",
  ) {
    let created = 0,
      new_versions = 0,
      unchanged = 0;
    const conversation_ids: string[] = [];
    for (const record of records) {
      const key = JSON.stringify([source, group_id, record.conversation_id]);
      const history = snapshots.get(key) ?? [];
      const fingerprint = JSON.stringify(record);
      let snapshot = history.find(
        (s) => s.fingerprint === fingerprint,
      )?.conversation;
      if (snapshot) unchanged++;
      else {
        if (history.length) new_versions++;
        else created++;
        const versionId = crypto.randomUUID();
        snapshot = {
          id: history[0]?.conversation.id ?? crypto.randomUUID(),
          external_id: record.conversation_id,
          source,
          group_id,
          language: record.language,
          channel: record.channel,
          current_transcript_version_id: versionId,
          transcript_version_id: versionId,
          message_count: record.transcript.length,
          audio_url: record.audio_url,
          latest_evaluation: null,
          messages: record.transcript.map((m, i) => ({
            ...m,
            id: `m${String(i + 1).padStart(4, "0")}`,
            position: i + 1,
          })),
        };
        history.push({ fingerprint, conversation: snapshot });
        snapshots.set(key, history);
      }
      const index = conversations.findIndex((c) => c.id === snapshot.id);
      if (index === -1) conversations.push(snapshot);
      else conversations[index] = snapshot;
      conversation_ids.push(snapshot.id);
    }
    return {
      group_id,
      received: records.length,
      created,
      new_versions,
      unchanged,
      conversation_ids,
    };
  }
  importData(demoRecords, "SYNTHETIC", "dataset");
  const response = (data: unknown, status = 200) =>
    new Response(JSON.stringify(data), {
      status,
      headers: { "Content-Type": "application/json" },
    });
  const failure = (code: string, status: number) =>
    response(
      { error: { code, message: "Synthetic demo response", details: [] } },
      status,
    );
  const paginate = <T>(items: T[], params: URLSearchParams) => {
    const limit = Number(params.get("limit") ?? 50),
      offset = Number(params.get("offset") ?? 0);
    return {
      items: items.slice(offset, offset + limit),
      total: items.length,
      limit,
      offset,
    };
  };
  return async (path, init) => {
    await new Promise((resolve) => setTimeout(resolve, 120));
    if (init.signal?.aborted) throw new DOMException("Cancelled", "AbortError");
    const { pathname, searchParams } = new URL(path, "http://synthetic.local");
    const parts = pathname.split("/").filter(Boolean);
    const body = init.body ? JSON.parse(String(init.body)) : undefined;
    if (init.method === "POST") {
      if (pathname === "/evaluations") return failure("AI_NOT_CONFIGURED", 503);
      if (pathname === "/datasets/import")
        return response(
          importData(demoRecords, String(body.group_id).trim(), "dataset"),
        );
      if (pathname === "/conversations/import") {
        const parsed = importSchema.safeParse(body);
        return parsed.success
          ? response(importData(parsed.data.records, "UPLOAD", "upload"))
          : failure("VALIDATION_ERROR", 422);
      }
      if (parts[0] === "checklists") {
        const parsed = criteriaSchema.safeParse(body.criteria);
        if (!parsed.success) return failure("VALIDATION_ERROR", 422);
        const previous = versions
          .filter((v) => v.checklist_id === parts[1])
          .at(-1);
        if (parts.length > 1 && !previous) return failure("NOT_FOUND", 404);
        if (
          previous &&
          previous.version_number !== body.expected_version_number
        )
          return failure("VERSION_CONFLICT", 409);
        const version = {
          id: crypto.randomUUID(),
          checklist_id: previous?.checklist_id ?? crypto.randomUUID(),
          name: previous?.name ?? body.name,
          version_number: (previous?.version_number ?? 0) + 1,
          created_at: new Date().toISOString(),
          criteria: parsed.data,
        };
        versions.push(version);
        return response(version, 201);
      }
    } else {
      if (pathname === "/conversations")
        return response(
          paginate(
            conversations
              .filter(
                (c) =>
                  (!searchParams.get("language") ||
                    c.language === searchParams.get("language")) &&
                  (!searchParams.get("channel") ||
                    c.channel === searchParams.get("channel")),
              )
              .map((c) => {
                const {
                  audio_url: _a,
                  messages: _m,
                  transcript_version_id: _t,
                  ...summary
                } = c;
                return summary;
              }),
            searchParams,
          ),
        );
      if (parts[0] === "conversations") {
        const conversation = conversations.find((c) => c.id === parts[1]);
        return conversation
          ? response(conversation)
          : failure("NOT_FOUND", 404);
      }
      if (pathname === "/checklists") {
        const ids = [...new Set(versions.map((v) => v.checklist_id))];
        return response(
          paginate(
            ids.map((id) => {
              const latest = versions
                .filter((v) => v.checklist_id === id)
                .at(-1)!;
              return {
                id,
                name: latest.name,
                latest_version_id: latest.id,
                latest_version_number: latest.version_number,
              };
            }),
            searchParams,
          ),
        );
      }
      if (parts[0] === "checklists") {
        const items = versions.filter((v) => v.checklist_id === parts[1]);
        const latest = items.at(-1);
        return latest
          ? response({
              id: latest.checklist_id,
              name: latest.name,
              current_version: latest,
              versions: items,
            })
          : failure("NOT_FOUND", 404);
      }
      if (parts[0] === "checklist-versions") {
        const version = versions.find((v) => v.id === parts[1]);
        return version ? response(version) : failure("NOT_FOUND", 404);
      }
    }
    return failure("NOT_FOUND", 404);
  };
}
