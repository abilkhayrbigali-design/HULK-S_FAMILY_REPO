import { test, expect } from "@playwright/test";
import { readFileSync } from "node:fs";
import {
  checklistDetailSchema,
  checklistVersionSchema,
  checklistSummarySchema,
  conversationSchema,
  conversationSummarySchema,
  importSchema,
  pageSchema,
  criterionSchema,
} from "../src/api/schema";
const fixture = (name: string) =>
  JSON.parse(
    readFileSync(
      new URL(`../../data/fixtures/synthetic/${name}`, import.meta.url),
      "utf8",
    ),
  );
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
