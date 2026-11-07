import { test, expect } from "@playwright/test";
import { demoRecords } from "../src/mock/fixtures";

test("real SQLite/API: JSON import, reload, checklist save/history and known contract gap", async ({
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
  // Backend metadata names differ from the target; do not hide the integration blocker.
  await expect(page.getByRole("alert")).toContainText(
    "не соответствует общему контракту",
  );
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
});
