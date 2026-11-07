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
const latestEvaluation = z
  .object({
    id,
    status: z.enum(["queued", "running", "completed", "failed"]),
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
