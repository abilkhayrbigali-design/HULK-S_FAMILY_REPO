import { test, expect } from "@playwright/test";
import { demoRecords } from "../src/mock/fixtures";

test("production app: JSON import, checklist history, reload and disabled AI", async ({
  page,
  request,
}) => {
  test.skip(
    process.env.CQA_TEST_BACKEND !== "1",
    "Enable explicit temporary-backend integration run",
  );
  await page.goto("/?mode=api");
  await expect(
    page.getByRole("heading", { name: "Диалогов пока нет" }),
  ).toBeVisible();
  await page.getByLabel("Загрузить JSON", { exact: true }).setInputFiles({
    name: "synthetic.json",
    mimeType: "application/json",
    buffer: Buffer.from(JSON.stringify({ records: demoRecords })),
  });
  await expect(page.locator("tbody tr")).toHaveCount(3);
  await page.getByRole("link", { name: "SYN-RU-001", exact: true }).click();
  await expect(page.locator("time").first()).toHaveText("00:00");
  await page.reload();
  await expect(page.getByText("Соединяю с оператором.")).toBeVisible();
  const list = await (await request.get("/api/v1/checklists")).json();
  expect(list.total).toBe(1);
  const initial = list.items[0];
  await page
    .getByRole("combobox", { name: "Чек-лист", exact: true })
    .selectOption(initial.id);
  await expect(
    page.getByRole("combobox", { name: "Версия", exact: true }),
  ).toHaveValue(initial.latest_version_id);
  await page.getByRole("button", { name: "Запустить оценку" }).click();
  await expect(page.getByRole("alert")).toContainText(
    "Провайдер оценки пока недоступен",
  );
  await expect(
    page.getByRole("heading", { name: "Финальная оценка" }),
  ).toHaveCount(0);
  await page.goto("/checklists?mode=api");
  await expect(
    page.getByRole("link", { name: new RegExp(initial.name) }),
  ).toBeVisible();
  await page.goto("/checklists/new?mode=api");
  await page
    .getByLabel("Название чек-листа", { exact: true })
    .fill("Синтетическая проверка SQLite");
  await page
    .getByLabel("Название критерия", { exact: true })
    .fill("Свой критерий");
  await page
    .getByLabel("Инструкция для оценки")
    .fill("Оператор объяснил следующий шаг.");
  await page
    .getByRole("button", { name: "Создать чек-лист", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Синтетическая проверка SQLite" }),
  ).toBeVisible();
  const id = page.url().split("/checklists/")[1].split("?")[0];
  const original = await (await request.get(`/api/v1/checklists/${id}`)).json();
  await page.getByLabel("Вес", { exact: true }).fill("35");
  await page.getByRole("button", { name: "Сохранить новую версию" }).click();
  await expect(page.getByRole("status")).toContainText("Версия 2 сохранена");
  await page.reload();
  await expect(page.getByLabel("Вес", { exact: true })).toHaveValue("35");
  const old = await (
    await request.get(
      `/api/v1/checklist-versions/${original.current_version.id}`,
    )
  ).json();
  expect(old.criteria[0].weight).toBe(10);
  const changed = await (await request.get(`/api/v1/checklists/${id}`)).json();
  expect(
    changed.versions.map(
      (version: { version_number: number }) => version.version_number,
    ),
  ).toEqual([1, 2]);
  const conversations = await (
    await request.get("/api/v1/conversations")
  ).json();
  const unavailable = await request.post("/api/v1/evaluations", {
    data: {
      conversation_id: conversations.items[0].id,
      checklist_version_id: changed.current_version.id,
    },
  });
  expect(unavailable.status()).toBe(503);
  expect((await unavailable.json()).error.code).toBe("AI_NOT_CONFIGURED");
  expect(
    (await (await request.get("/api/v1/evaluations")).json()).items,
  ).toEqual([]);
});
