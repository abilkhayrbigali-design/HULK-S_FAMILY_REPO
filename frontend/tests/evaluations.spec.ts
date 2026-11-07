import { expect, test, type Page, type Route } from "@playwright/test";
import {
  appealSchema,
  checklistDetailSchema,
  conversationSchema,
  decisionInputSchema,
  decisionResponseSchema,
  evaluationSchema,
  type Appeal,
  type DecisionInput,
  type Evaluation,
} from "../src/api/schema";
import { fixture } from "./synthetic";

const completed = evaluationSchema.parse(fixture("evaluation.completed.json"));
const conversation = conversationSchema.parse(
  fixture("conversation.response.json"),
);
const checklist = checklistDetailSchema.parse(
  fixture("checklist.response.json"),
);
const latestVersion = {
  ...checklist.current_version,
  id: "00000000-0000-4000-8000-000000000010",
  version_number: 2,
};
const newerTranscript = "00000000-0000-4000-8000-000000000011";
const marker = "SYNTHETIC_PRIVATE_EVALUATION_MARKER";
const url = `/conversations/${conversation.id}?mode=api`;
const error = (code: string) => ({
  error: { code, message: marker, details: [marker] },
});
const asPage = (items: unknown[], limit = 50) => ({
  items,
  total: items.length,
  limit,
  offset: 0,
});

async function syntheticApi(
  page: Page,
  state: {
    run?: Evaluation;
    startError?: "network" | "503";
    newerTranscript?: boolean;
    decide?: (route: Route, input: DecisionInput) => Promise<void>;
    appeals?: Appeal[];
  } = {},
) {
  const posts: unknown[] = [];
  const decisions: DecisionInput[] = [];
  const transcriptReads: string[] = [];
  let polls = 0;
  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const address = new URL(request.url());
    const path = address.pathname.replace(/^\/api\/v1/, "");
    const post = request.method() === "POST";
    if (path === `/conversations/${conversation.id}` && !post) {
      const pin = address.searchParams.get("transcript_version_id");
      if (pin) transcriptReads.push(pin);
      const current = state.newerTranscript
        ? newerTranscript
        : conversation.transcript_version_id;
      return route.fulfill({
        json: {
          ...conversation,
          latest_evaluation: state.run
            ? {
                id: state.run.id,
                status: state.run.status,
                original_total: state.run.original?.total ?? null,
                final_total: state.run.final?.total ?? null,
                created_at: state.run.created_at,
              }
            : null,
          current_transcript_version_id: current,
          transcript_version_id: pin ?? current,
          messages:
            state.newerTranscript && !pin
              ? conversation.messages.map((message) => ({
                  ...message,
                  text: "Более новый транскрипт",
                }))
              : conversation.messages,
        },
      });
    }
    if (path === "/conversations" && !post) {
      return route.fulfill({ json: fixture("conversations.response.json") });
    }
    if (path === "/checklists" && !post)
      return route.fulfill({ json: fixture("checklists.response.json") });
    if (path === `/checklists/${checklist.id}` && !post)
      return route.fulfill({
        json: {
          ...checklist,
          current_version: latestVersion,
          versions: [...checklist.versions, latestVersion],
        },
      });
    if (path === `/checklist-versions/${checklist.current_version.id}` && !post)
      return route.fulfill({ json: checklist.current_version });
    if (path === "/evaluations" && post) {
      posts.push(request.postDataJSON());
      if (state.startError === "503")
        return route.fulfill({ status: 503, json: error("AI_NOT_CONFIGURED") });
      state.run = evaluationSchema.parse(fixture("evaluation.queued.json"));
      if (state.startError === "network") return route.abort("failed");
      return route.fulfill({ status: 202, json: state.run });
    }
    if (path === `/evaluations/${completed.id}` && state.run && !post) {
      polls++;
      return route.fulfill({ json: state.run });
    }
    if (
      path === "/appeals" &&
      !post &&
      address.searchParams.get("evaluation_id") === completed.id
    )
      return route.fulfill({ json: asPage(state.appeals ?? []) });
    if (path === `/evaluations/${completed.id}/appeals` && post) {
      const input = request.postDataJSON();
      const row = completed.original?.criteria.find(
        (criterion) => criterion.criterion_id === input.criterion_id,
      );
      const appeal = appealSchema.parse({
        ...fixture("appeal.open.json"),
        criterion_id: input.criterion_id,
        comment: input.comment,
        base_revision: state.run?.revision,
        current_revision: state.run?.revision,
        original: row,
        current: row,
      });
      state.appeals = [appeal];
      return route.fulfill({ status: 201, json: appeal });
    }
    const appeal = state.appeals?.[0];
    if (appeal && path === `/appeals/${appeal.id}` && !post)
      return route.fulfill({ json: appeal });
    if (
      appeal &&
      path === `/appeals/${appeal.id}/decisions` &&
      post &&
      state.decide
    ) {
      const input = decisionInputSchema.parse(request.postDataJSON());
      decisions.push(input);
      return state.decide(route, input);
    }
    return route.fulfill({ status: 404, json: error("NOT_FOUND") });
  });
  return { posts, decisions, transcriptReads, polls: () => polls };
}

async function selectChecklist(page: Page) {
  await page
    .getByRole("combobox", { name: "Чек-лист", exact: true })
    .selectOption(checklist.id);
  const version = page.getByRole("combobox", { name: "Версия", exact: true });
  await expect(version).toHaveValue(latestVersion.id);
  await version.selectOption(checklist.current_version.id);
}

function scores(page: Page) {
  return {
    original: page
      .locator(".score-summary")
      .filter({ has: page.getByRole("heading", { name: "Исходная оценка" }) }),
    final: page
      .locator(".score-summary")
      .filter({ has: page.getByRole("heading", { name: "Финальная оценка" }) }),
  };
}

test("synthetic evaluation: single POST, statuses, pinned evidence and reload", async ({
  page,
}) => {
  const state: Parameters<typeof syntheticApi>[1] = {};
  const api = await syntheticApi(page, state);
  await page.clock.install();
  await page.goto(url);
  await selectChecklist(page);
  const start = page.getByRole("button", { name: "Запустить оценку" });
  await start.evaluate((button) => {
    (button as HTMLButtonElement).click();
    (button as HTMLButtonElement).click();
  });
  await expect(page.getByText("В очереди", { exact: true })).toBeVisible();
  await expect(start).toBeDisabled();
  expect(api.posts).toEqual([
    {
      conversation_id: conversation.id,
      checklist_version_id: checklist.current_version.id,
    },
  ]);
  state.run = evaluationSchema.parse(fixture("evaluation.running.json"));
  await page.clock.runFor(1_050);
  await expect(
    page.getByText("Оценка выполняется", { exact: true }),
  ).toBeVisible();
  state.run = completed;
  await page.clock.runFor(1_050);
  await expect(
    page.getByText("Оценка завершена", { exact: true }),
  ).toBeVisible();
  await expect(scores(page).original).toContainText("70 / 100");
  await expect(scores(page).final).toContainText("70 / 100");
  await expect(
    page.getByRole("heading", { name: "Понятность инструкции", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("N/A · Не применимо", { exact: true }).first(),
  ).toBeVisible();
  await expect(
    page.getByText("Грубые нарушения: критерий отсутствует").first(),
  ).toBeVisible();

  state.newerTranscript = true;
  await page.reload();
  await expect(
    page.getByText("Показана версия транскрипта, сохранённая для этой оценки."),
  ).toBeVisible();
  const reference = page
    .getByRole("button", { name: "Реплика m0002", exact: true })
    .first();
  await reference.focus();
  await page.keyboard.press("Enter");
  await expect(page.locator("#transcript-message-m0002")).toBeFocused();
  await expect(page.locator(".evidence-target")).toHaveCount(1);
  const range = page
    .getByRole("button", { name: "Диапазон m0001–m0004", exact: true })
    .first();
  await range.click();
  await expect(page.locator("#transcript-message-m0001")).toBeFocused();
  await expect(page.locator(".evidence-target")).toHaveCount(4);
  await expect(page.getByText("Более новый транскрипт")).toHaveCount(0);
  expect(api.transcriptReads).toContain(conversation.transcript_version_id);
  await page.goto(url);
  await expect(scores(page).final).toContainText("70 / 100");
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(scores(page).final).toBeVisible();
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth),
  ).toBeLessThanOrEqual(390);
  expect(api.posts).toHaveLength(1);
});

test("polling stops when the user leaves a pending evaluation", async ({
  page,
}) => {
  const api = await syntheticApi(page, {
    run: evaluationSchema.parse(fixture("evaluation.queued.json")),
  });
  await page.goto(url);
  await expect(page.getByText("В очереди", { exact: true })).toBeVisible();
  await page.getByRole("link", { name: "Все диалоги" }).click();
  const reads = api.polls();
  await page.waitForTimeout(1_150);
  expect(api.polls()).toBe(reads);
});

for (const condition of ["failed", "503", "network"] as const) {
  test(`evaluation failure is shown safely: ${condition}`, async ({ page }) => {
    const logs: string[] = [];
    page.on("console", (event) => {
      if (event.text().startsWith("cqa.ui")) logs.push(event.text());
    });
    const failed = evaluationSchema.parse({
      ...fixture("evaluation.failed.json"),
      error: { code: "AI_PROVIDER_ERROR", message: marker, details: [marker] },
    });
    const api = await syntheticApi(
      page,
      condition === "failed" ? { run: failed } : { startError: condition },
    );
    await page.goto(url);
    if (condition !== "failed") {
      await selectChecklist(page);
      await page.getByRole("button", { name: "Запустить оценку" }).click();
    }
    await expect(page.getByRole("alert")).toContainText(
      {
        failed: "Модель завершила запуск с ошибкой",
        "503": "Провайдер оценки пока недоступен",
        network: "Нет соединения с сервером",
      }[condition],
    );
    await expect(scores(page).final).toHaveCount(0);
    await expect(page.getByText(marker)).toHaveCount(0);
    expect(logs.join("\n")).toContain(
      {
        failed:
          "event=evaluation_run_failed status=200 error=AI_PROVIDER_ERROR",
        "503":
          "event=evaluation_start_failed status=503 error=AI_NOT_CONFIGURED",
        network: "event=evaluation_start_failed status=0 error=NETWORK_ERROR",
      }[condition],
    );
    expect(logs.join("\n")).not.toContain(marker);
    if (condition === "network") {
      await expect(
        page.getByRole("button", { name: "Запустить оценку" }),
      ).toBeDisabled();
      await page
        .getByRole("button", { name: "Проверить последний запуск" })
        .click();
      await expect(page.getByText("В очереди", { exact: true })).toBeVisible();
      expect(api.posts).toHaveLength(1);
    }
  });
}

test("appeal override: stale revision preserves the draft and requires explicit retry", async ({
  page,
}) => {
  const state: Parameters<typeof syntheticApi>[1] = { run: completed };
  state.decide = async (route, input) => {
    if (input.expected_revision !== state.run?.revision)
      return route.fulfill({ status: 409, json: error("VERSION_CONFLICT") });
    const resolved = decisionResponseSchema.parse(
      fixture("decision.response.json"),
    );
    const appeal = appealSchema.parse({
      ...resolved.appeal,
      comment: state.appeals?.[0].comment,
      current_revision: input.expected_revision + 1,
      decision: {
        ...resolved.appeal.decision,
        expected_revision: input.expected_revision,
        reason: input.reason,
      },
    });
    state.run = {
      ...resolved.evaluation,
      revision: input.expected_revision + 1,
    };
    state.appeals = [appeal];
    await route.fulfill({ json: { appeal, evaluation: state.run } });
  };
  const api = await syntheticApi(page, state);
  await page.goto(url);
  await expect(page.getByText("Апелляций пока нет.")).toBeVisible();
  await page
    .getByLabel("Критерий для апелляции")
    .selectOption("navigation_custom");
  await page
    .getByLabel("Комментарий к апелляции")
    .fill("Синтетическое обращение по шкале");
  await page.getByRole("button", { name: "Отправить апелляцию" }).click();
  await expect(
    page.getByText("Синтетическое обращение по шкале", { exact: true }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Изменить ответ", exact: true })
    .click();
  await expect(
    page.getByRole("heading", {
      name: "Изменение ответа · Понятность инструкции",
    }),
  ).toBeFocused();
  const reason = page.getByLabel("Причина решения");
  await reason.fill("Синтетическая причина пересмотра");
  await page.getByLabel("Новый ответ").selectOption("10");
  await page.getByLabel("Вид доказательства 1").selectOption("range");
  await page.getByLabel("Конец диапазона 1").selectOption("m0001");
  const save = page.getByRole("button", { name: "Сохранить решение" });
  await save.click();
  await expect(page.getByRole("alert")).toContainText("в порядке реплик");
  expect(api.decisions).toHaveLength(0);
  await page.getByLabel("Конец диапазона 1").selectOption("m0004");
  state.run = { ...completed, revision: 1 };
  state.appeals = state.appeals?.map((appeal) => ({
    ...appeal,
    current_revision: 1,
  }));
  await save.click();
  await expect(page.getByRole("alert")).toContainText("Конфликт ревизии");
  await expect(reason).toHaveValue("Синтетическая причина пересмотра");
  await expect(page.getByLabel("Новый ответ")).toHaveValue("10");
  await expect(save).toBeDisabled();
  expect(api.decisions.map((item) => item.expected_revision)).toEqual([0]);
  await page.getByRole("button", { name: "Продолжить с ревизией 1" }).click();
  await save.click();
  await expect(page.getByText("Решение сохранено.")).toBeVisible();
  expect(api.decisions.at(-1)).toEqual({
    action: "override",
    reason: "Синтетическая причина пересмотра",
    expected_revision: 1,
    applicability: "applicable",
    answer: 10,
    evidence: [
      { kind: "range", start_message_id: "m0002", end_message_id: "m0004" },
    ],
  });
  await expect(scores(page).original).toContainText("70 / 100");
  await expect(scores(page).final).toContainText("100 / 100");
  await page.reload();
  await expect(scores(page).original).toContainText("70 / 100");
  await expect(scores(page).final).toContainText("100 / 100");
  await expect(
    page.getByText("Причина решения: Синтетическая причина пересмотра"),
  ).toBeVisible();
});

test("appeal confirm sends only the reason and expected revision", async ({
  page,
}) => {
  const open = appealSchema.parse(fixture("appeal.open.json"));
  const state: Parameters<typeof syntheticApi>[1] = {
    run: completed,
    appeals: [open],
  };
  state.decide = async (route, input) => {
    const saved = decisionResponseSchema.parse(
      fixture("decision.response.json"),
    );
    const appeal = appealSchema.parse({
      ...open,
      status: "resolved",
      current_revision: 1,
      decision: { ...saved.appeal.decision, ...input, result: open.current },
    });
    state.run = { ...completed, revision: 1 };
    state.appeals = [appeal];
    await route.fulfill({ json: { evaluation: state.run, appeal } });
  };
  const api = await syntheticApi(page, state);
  await page.goto(url);
  await page
    .getByRole("button", { name: "Подтвердить ответ", exact: true })
    .click();
  const save = page.getByRole("button", { name: "Сохранить решение" });
  await expect(save).toBeDisabled();
  await page.getByLabel("Причина решения").fill("Синтетическое подтверждение");
  await save.click();
  await expect(page.getByText("Ответ подтверждён")).toBeVisible();
  expect(api.decisions).toEqual([
    {
      action: "confirm",
      reason: "Синтетическое подтверждение",
      expected_revision: 0,
    },
  ]);
  await expect(scores(page).final).toContainText("70 / 100");
});
