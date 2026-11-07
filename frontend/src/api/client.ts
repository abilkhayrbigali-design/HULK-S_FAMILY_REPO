import { z } from "zod";
import { ApiError, logEvent, safeCode, type Event } from "./errors";
import {
  checklistDetailSchema,
  checklistSummarySchema,
  checklistVersionSchema,
  conversationSchema,
  conversationSummarySchema,
  importSummarySchema,
  pageSchema,
  type Channel,
  type Criterion,
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
    operation: "load" | "import" | "save" = "load",
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
        headers: { "Content-Type": "application/json" },
        body: body === undefined ? undefined : JSON.stringify(body),
        signal: controller.signal,
      });
      let raw: unknown;
      try {
        raw = await response.json();
      } catch {
        throw new ApiError("INVALID_RESPONSE", response.status);
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
      const safe =
        error instanceof ApiError
          ? error
          : new ApiError(
              controller.signal.aborted ? "TIMEOUT" : "NETWORK_ERROR",
            );
      logEvent(`${operation}_failed` as Event, safe.status, safe.code);
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
  conversation(id: string, signal?: AbortSignal) {
    return this.request(
      `/conversations/${encodeURIComponent(id)}`,
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
