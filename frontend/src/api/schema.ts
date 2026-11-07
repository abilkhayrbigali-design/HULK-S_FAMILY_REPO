import { z } from "zod";

const text = z.string().trim().min(1, "Заполните поле");
const id = z.string().uuid();
const number = z.number().finite().nonnegative();
export const languageSchema = z.enum(["RU", "KZ", "EN"]);
export const channelSchema = z.enum(["agent chat", "voice call"]);
const speaker = z.enum(["client", "operator", "bot", "system"]);

// URLs are never requested automatically; the user explicitly opens an audio link.
export function safeAudioUrl(value: string): boolean {
  try {
    const url = new URL(value);
    return (
      /^https?:$/.test(url.protocol) &&
      !!url.hostname &&
      !url.username &&
      !url.password &&
      !/[\s\u0000-\u001f\u007f]/.test(value)
    );
  } catch {
    return false;
  }
}
const audio = z
  .string()
  .refine(safeAudioUrl, "Нужна корректная HTTP(S)-ссылка")
  .nullable();
const messageInput = z.strictObject({
  timestamp: z.string(),
  speaker,
  text: z.string().min(1),
});
export const organizerRecordSchema = z
  .object({
    conversation_id: text,
    language: languageSchema,
    channel: channelSchema,
    transcript: z.array(messageInput).min(1),
    audio_url: audio.default(null),
  })
  .strict()
  .refine((r) => r.channel !== "agent chat" || r.audio_url === null, {
    message: "У чата audio_url должен быть null",
  });
export const importSchema = z
  .object({ records: z.array(organizerRecordSchema).min(1) })
  .strict()
  .refine(
    (p) =>
      new Set(p.records.map((r) => r.conversation_id)).size ===
      p.records.length,
    { message: "ID диалогов в файле должны быть уникальны" },
  );

export const criterionSchema = z
  .object({
    id: text,
    name: text,
    instruction: text,
    type: z.enum(["binary", "scale", "flag"]),
    weight: number,
    allow_na: z.boolean(),
    na_rule: text.nullable(),
    scale: z
      .array(z.strictObject({ value: number, description: text }))
      .nullable(),
    section: z.string().nullable(),
  })
  .strict()
  .superRefine((c, ctx) => {
    const issue = (path: string, message: string) =>
      ctx.addIssue({ code: "custom", path: [path], message });
    if (c.allow_na !== (c.na_rule !== null))
      issue("na_rule", "Для N/A задайте условие; иначе оставьте null");
    if (c.type === "flag") {
      if (c.weight !== 0 || c.allow_na || c.na_rule !== null)
        issue("weight", "Флаг: вес 0, без N/A");
    } else if (c.weight <= 0) issue("weight", "Вес должен быть больше нуля");
    if (c.type === "scale") {
      const values = (c.scale ?? []).map((s) => s.value);
      if (
        values.length < 2 ||
        new Set(values).size !== values.length ||
        Math.max(...values) <= 0
      )
        issue(
          "scale",
          "Нужны минимум две разные ступени; максимум больше нуля",
        );
    } else if (c.scale !== null)
      issue("scale", "Ступени допустимы только для числовой шкалы");
  });
export const criteriaSchema = z
  .array(criterionSchema)
  .min(1, "Добавьте хотя бы один критерий")
  .refine(
    (cs) => new Set(cs.map((c) => c.id)).size === cs.length,
    "ID критериев должны быть уникальны",
  )
  .refine(
    (cs) => cs.some((c) => c.type !== "flag"),
    "Нужен хотя бы один критерий с положительным весом",
  );
export const checklistVersionSchema = z.strictObject({
  id,
  checklist_id: id,
  name: text,
  version_number: z.number().int().positive(),
  created_at: z.string(),
  criteria: criteriaSchema,
});
export const checklistSummarySchema = z.strictObject({
  id,
  name: text,
  latest_version_id: id,
  latest_version_number: z.number().int().positive(),
});
export const checklistDetailSchema = z.strictObject({
  id,
  name: text,
  current_version: checklistVersionSchema,
  versions: z.array(checklistVersionSchema).min(1),
});
const evaluationStatus = z.enum(["queued", "running", "completed", "failed"]);
const latestEvaluation = z
  .object({
    id,
    status: evaluationStatus,
    original_total: number.nullable(),
    final_total: number.nullable(),
    created_at: z.string(),
  })
  .nullable();
export const conversationSummarySchema = z.object({
  id,
  external_id: text,
  source: z.enum(["dataset", "upload"]),
  group_id: text,
  language: languageSchema,
  channel: channelSchema,
  current_transcript_version_id: id,
  message_count: z.number().int().nonnegative(),
  latest_evaluation: latestEvaluation,
});
export const conversationSchema = conversationSummarySchema.extend({
  transcript_version_id: id,
  audio_url: audio,
  messages: z.array(
    messageInput.extend({ id: text, position: z.number().int().positive() }),
  ),
});

const evidence = z.discriminatedUnion("kind", [
  z.object({ kind: z.literal("message"), message_id: text }),
  z.object({
    kind: z.literal("range"),
    start_message_id: text,
    end_message_id: text,
  }),
]);
const applicability = z.enum(["applicable", "not_applicable"]);
const answer = z.union([z.boolean(), number, z.null()]);
const criterionResult = z.object({
  criterion_id: text,
  applicability,
  answer,
  explanation: text,
  evidence: z.array(evidence).min(1),
  review_reasons: z.array(text),
});
const criterionAssessment = criterionResult.extend({
  weight: number,
  base_points: number,
  effective_weight: number,
  effective_points: number,
  review_required: z.boolean(),
});
const totalScore = number.max(100).nullable();
const assessment = z.object({
  total: totalScore,
  no_applicable_reason: text.nullable(),
  gross_error: z.boolean().nullable(),
  criteria: z.array(criterionAssessment).min(1),
  sections: z.array(
    z.object({
      name: text.nullable(),
      base_points: number,
      applicable_weight: number,
      total: totalScore,
    }),
  ),
});
const counter = z.number().int().nonnegative();
const provenance = z.object({
  provider: text,
  model_requested: text,
  model_reported: text.nullable(),
  prompt_version: text,
  duration_ms: counter,
  usage: z.object({
    request_count: counter,
    input_tokens: counter.nullable(),
    output_tokens: counter.nullable(),
    total_tokens: counter.nullable(),
    cached_input_tokens: counter.nullable(),
    reasoning_output_tokens: counter.nullable(),
  }),
  estimated_cost_usd: number.nullable(),
});
const evaluationBase = z.object({
  id,
  conversation_id: id,
  transcript_version_id: id,
  checklist_version_id: id,
  revision: counter,
  created_at: z.string(),
  review_required: z.boolean(),
  review_reasons: z.array(text),
});
export const evaluationSchema = z.discriminatedUnion("status", [
  evaluationBase.extend({
    status: z.enum(["queued", "running"]),
    completed_at: z.null(),
    original: z.null(),
    final: z.null(),
    provenance: provenance.nullable(),
    error: z.null(),
  }),
  evaluationBase.extend({
    status: z.literal("completed"),
    completed_at: z.string(),
    original: assessment,
    final: assessment,
    provenance,
    error: z.null(),
  }),
  evaluationBase.extend({
    status: z.literal("failed"),
    completed_at: z.string(),
    original: z.null(),
    final: z.null(),
    provenance: provenance.nullable(),
    error: z.object({
      code: text,
      message: z.string(),
      details: z.array(z.unknown()),
    }),
  }),
]);
const decisionBase = z.object({ reason: text, expected_revision: counter });
export const decisionInputSchema = z.discriminatedUnion("action", [
  decisionBase.extend({ action: z.literal("confirm") }),
  decisionBase.extend({
    action: z.literal("override"),
    applicability,
    answer,
    evidence: z.array(evidence).min(1),
  }),
]);
const decision = decisionBase.extend({
  id,
  appeal_id: id,
  action: z.enum(["confirm", "override"]),
  created_at: z.string(),
  result: criterionResult,
});
const appealBase = z.object({
  id,
  evaluation_id: id,
  criterion_id: text,
  comment: text,
  base_revision: counter,
  current_revision: counter,
  created_at: z.string(),
  original: criterionAssessment,
  current: criterionAssessment,
});
export const appealSchema = z.discriminatedUnion("status", [
  appealBase.extend({ status: z.literal("open"), decision: z.null() }),
  appealBase.extend({ status: z.literal("resolved"), decision }),
]);
export const decisionResponseSchema = z.object({
  appeal: appealSchema,
  evaluation: evaluationSchema,
});
export type Evaluation = z.infer<typeof evaluationSchema>;
export type CompletedEvaluation = Extract<Evaluation, { status: "completed" }>;
export type Assessment = z.infer<typeof assessment>;
export type CriterionResult = z.infer<typeof criterionResult>;
export type CriterionAssessment = z.infer<typeof criterionAssessment>;
export type Evidence = z.infer<typeof evidence>;
export type Appeal = z.infer<typeof appealSchema>;
export type OpenAppeal = Extract<Appeal, { status: "open" }>;
export type DecisionInput = z.infer<typeof decisionInputSchema>;
export const importSummarySchema = z.object({
  group_id: text,
  received: z.number().int().nonnegative(),
  created: z.number().int().nonnegative(),
  new_versions: z.number().int().nonnegative(),
  unchanged: z.number().int().nonnegative(),
  conversation_ids: z.array(id),
});
export const pageSchema = <T extends z.ZodType>(item: T) =>
  z.object({
    items: z.array(item),
    total: z.number().int().nonnegative(),
    limit: z.number().int().min(1).max(100),
    offset: z.number().int().nonnegative(),
  });
export type Criterion = z.infer<typeof criterionSchema>;
export type ChecklistVersion = z.infer<typeof checklistVersionSchema>;
export type ChecklistSummary = z.infer<typeof checklistSummarySchema>;
export type ChecklistDetail = z.infer<typeof checklistDetailSchema>;
export type Conversation = z.infer<typeof conversationSchema>;
export type ConversationSummary = z.infer<typeof conversationSummarySchema>;
export type OrganizerRecord = z.infer<typeof organizerRecordSchema>;
export type ImportSummary = z.infer<typeof importSummarySchema>;
export type Language = z.infer<typeof languageSchema>;
export type Channel = z.infer<typeof channelSchema>;
export type Page<T> = {
  items: T[];
  total: number;
  limit: number;
  offset: number;
};
