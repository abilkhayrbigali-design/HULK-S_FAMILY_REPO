import { z } from "zod";
import { ApiError, logEvent, safeCode, type Mutation } from "./errors";
import {
  checklistDetailSchema,
  checklistSummarySchema,
  checklistVersionSchema,
  conversationSchema,
  conversationSummarySchema,
  appealSchema,
  decisionResponseSchema,
  evaluationSchema,
  importSummarySchema,
  pageSchema,
  type Channel,
  type Criterion,
  type DecisionInput,
  type Language,
} from "./schema";

export type Filters = {
  language?: Language;
  channel?: Channel;
  limit?: number;
  offset?: number;
};
export type Transport = (path: string, init: RequestInit) => Promise<Response>;
export class ApiClient {
  constructor(
    private transport: Transport = (path, init) =>
      fetch(`/api/v1${path}`, init),
    private timeoutMs = 30_000,
  ) {}
  private async request<T>(
    path: string,
    schema: z.ZodType<T>,
    body?: unknown,
    operation: "load" | Mutation = "load",
    signal?: AbortSignal,
  ): Promise<T> {
    const controller = new AbortController();
    const abort = () => controller.abort();
    signal?.addEventListener("abort", abort, { once: true });
    if (signal?.aborted) controller.abort();
    const timeout = setTimeout(abort, this.timeoutMs);
    if (operation !== "load") logEvent(`${operation}_started`);
    try {
      const response = await this.transport(path, {
        method: body === undefined ? "GET" : "POST",
        cache: "no-store",
        headers: { "Content-Type": "application/json" },
        body: body === undefined ? undefined : JSON.stringify(body),
        signal: controller.signal,
      });
      let raw: unknown;
      try {
        raw = await response.json();
      } catch (error) {
        // Only malformed JSON is a contract error. A body stream may still
        // fail after headers have arrived; preserve cancellation/network errors.
        if (error instanceof SyntaxError && !controller.signal.aborted)
          throw new ApiError("INVALID_RESPONSE", response.status);
        throw error;
      }
      if (!response.ok) {
        const error = z
          .object({ error: z.object({ code: z.unknown() }) })
          .safeParse(raw);
        throw new ApiError(
          safeCode(
            error.success ? error.data.error.code : undefined,
            response.status,
          ),
          response.status,
        );
      }
      const parsed = schema.safeParse(raw);
      if (!parsed.success)
        throw new ApiError("INVALID_RESPONSE", response.status);
      if (operation !== "load")
        logEvent(`${operation}_completed`, response.status);
      return parsed.data;
    } catch (error) {
      if (signal?.aborted) throw new DOMException("Cancelled", "AbortError");
      const safe = controller.signal.aborted
        ? new ApiError("TIMEOUT")
        : error instanceof ApiError
          ? error
          : new ApiError("NETWORK_ERROR");
      logEvent(`${operation}_failed`, safe.status, safe.code);
      throw safe;
    } finally {
      clearTimeout(timeout);
      signal?.removeEventListener("abort", abort);
    }
  }
  conversations(filters: Filters = {}, signal?: AbortSignal) {
    const query = new URLSearchParams();
    Object.entries(filters).forEach(([key, value]) => {
      if (value !== undefined) query.set(key, String(value));
    });
    return this.request(
      `/conversations?${query}`,
      pageSchema(conversationSummarySchema),
      undefined,
      "load",
      signal,
    );
  }
  conversation(id: string, signal?: AbortSignal, transcriptVersion?: string) {
    const query = transcriptVersion
      ? `?${new URLSearchParams({ transcript_version_id: transcriptVersion })}`
      : "";
    return this.request(
      `/conversations/${encodeURIComponent(id)}${query}`,
      conversationSchema,
      undefined,
      "load",
      signal,
    );
  }
  checklists(offset = 0, signal?: AbortSignal) {
    return this.request(
      `/checklists?limit=100&offset=${offset}`,
      pageSchema(checklistSummarySchema),
      undefined,
      "load",
      signal,
    );
  }
  checklist(id: string, signal?: AbortSignal) {
    return this.request(
      `/checklists/${encodeURIComponent(id)}`,
      checklistDetailSchema,
      undefined,
      "load",
      signal,
    );
  }
  version(id: string, signal?: AbortSignal) {
    return this.request(
      `/checklist-versions/${encodeURIComponent(id)}`,
      checklistVersionSchema,
      undefined,
      "load",
      signal,
    );
  }
  startEvaluation(
    conversation_id: string,
    checklist_version_id: string,
    signal: AbortSignal,
  ) {
    return this.request(
      "/evaluations",
      evaluationSchema,
      { conversation_id, checklist_version_id },
      "evaluation_start",
      signal,
    );
  }
  evaluation(id: string, signal: AbortSignal) {
    return this.request(
      `/evaluations/${encodeURIComponent(id)}`,
      evaluationSchema,
      undefined,
      "load",
      signal,
    );
  }
  appeals(evaluation_id: string, offset: number, signal: AbortSignal) {
    const query = new URLSearchParams({
      evaluation_id,
      limit: "50",
      offset: String(offset),
    });
    return this.request(
      `/appeals?${query}`,
      pageSchema(appealSchema),
      undefined,
      "load",
      signal,
    );
  }
  appeal(id: string, signal: AbortSignal) {
    return this.request(
      `/appeals/${encodeURIComponent(id)}`,
      appealSchema,
      undefined,
      "load",
      signal,
    );
  }
  createAppeal(
    id: string,
    criterion_id: string,
    comment: string,
    signal: AbortSignal,
  ) {
    return this.request(
      `/evaluations/${encodeURIComponent(id)}/appeals`,
      appealSchema,
      { criterion_id, comment },
      "appeal",
      signal,
    );
  }
  decideAppeal(id: string, decision: DecisionInput, signal: AbortSignal) {
    return this.request(
      `/appeals/${encodeURIComponent(id)}/decisions`,
      decisionResponseSchema,
      decision,
      "decision",
      signal,
    );
  }
  importGroup(group_id: string) {
    return this.request(
      "/datasets/import",
      importSummarySchema,
      { group_id },
      "import",
    );
  }
  importRecords(records: unknown[]) {
    return this.request(
      "/conversations/import",
      importSummarySchema,
      { records },
      "import",
    );
  }
  createChecklist(name: string, criteria: Criterion[]) {
    return this.request(
      "/checklists",
      checklistVersionSchema,
      { name, criteria },
      "save",
    );
  }
  saveVersion(
    id: string,
    expected_version_number: number,
    criteria: Criterion[],
  ) {
    return this.request(
      `/checklists/${encodeURIComponent(id)}/versions`,
      checklistVersionSchema,
      { expected_version_number, criteria },
      "save",
    );
  }
}
