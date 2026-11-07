import { test, expect } from "@playwright/test";
import {
  checklistDetailSchema,
  checklistVersionSchema,
  checklistSummarySchema,
  conversationSchema,
  conversationSummarySchema,
  importSchema,
  pageSchema,
  criterionSchema,
  appealSchema,
  decisionInputSchema,
  decisionResponseSchema,
  evaluationSchema,
} from "../src/api/schema";
import { fixture } from "./synthetic";
test("runtime client schemas consume agreed shared fixtures", () => {
  expect(() =>
    importSchema.parse(fixture("import.request.json")),
  ).not.toThrow();
  expect(() =>
    conversationSchema.parse(fixture("conversation.response.json")),
  ).not.toThrow();
  expect(() =>
    pageSchema(conversationSummarySchema).parse(
      fixture("conversations.response.json"),
    ),
  ).not.toThrow();
  expect(() =>
    checklistVersionSchema.parse(fixture("checklist-version.response.json")),
  ).not.toThrow();
  expect(() =>
    checklistDetailSchema.parse(fixture("checklist.response.json")),
  ).not.toThrow();
  expect(() =>
    pageSchema(checklistSummarySchema).parse(
      fixture("checklists.response.json"),
    ),
  ).not.toThrow();
});

test("evaluation and appeal schemas accept the shared wire formats", () => {
  for (const status of ["queued", "running", "completed", "failed"])
    expect(
      evaluationSchema.safeParse(fixture(`evaluation.${status}.json`)).success,
    ).toBe(true);
  expect(
    pageSchema(evaluationSchema).safeParse(fixture("evaluations.response.json"))
      .success,
  ).toBe(true);
  expect(
    pageSchema(appealSchema).safeParse(fixture("appeals.response.json"))
      .success,
  ).toBe(true);
  expect(appealSchema.safeParse(fixture("appeal.open.json")).success).toBe(
    true,
  );
  expect(
    decisionResponseSchema.safeParse(fixture("decision.response.json")).success,
  ).toBe(true);
  for (const action of ["confirm", "override"])
    expect(
      decisionInputSchema.safeParse(fixture(`decision.${action}.json`)).success,
    ).toBe(true);
  const complete = fixture("evaluation.completed.json");
  const withoutSection = {
    ...complete,
    final: {
      ...complete.final,
      sections: [{ ...complete.final.sections[0], name: null }],
    },
  };
  expect(evaluationSchema.safeParse(withoutSection).success).toBe(true);
  expect(
    evaluationSchema.safeParse({ ...complete, original: null }).success,
  ).toBe(false);
  expect(
    evaluationSchema.safeParse({
      ...complete,
      final: { ...complete.final, total: "70" },
    }).success,
  ).toBe(false);
});
test("numeric definitions never coerce boolean/string/nonfinite values", () => {
  const c = fixture("checklist-version.response.json").criteria[0];
  for (const weight of [true, "10", NaN, Infinity, -1])
    expect(criterionSchema.safeParse({ ...c, weight }).success).toBe(false);
});

test("checklist wire schemas reject legacy fields and missing or null version metadata", () => {
  const summary = fixture("checklists.response.json").items[0];
  const detail = fixture("checklist.response.json");
  expect(
    checklistSummarySchema.safeParse({ ...summary, latest_version_id: null })
      .success,
  ).toBe(false);
  expect(
    checklistSummarySchema.safeParse({
      ...summary,
      latest_version_number: undefined,
    }).success,
  ).toBe(false);
  expect(
    checklistDetailSchema.safeParse({ ...detail, current_version_number: 1 })
      .success,
  ).toBe(false);
  expect(
    checklistDetailSchema.safeParse({ ...detail, current_version: null })
      .success,
  ).toBe(false);
});
