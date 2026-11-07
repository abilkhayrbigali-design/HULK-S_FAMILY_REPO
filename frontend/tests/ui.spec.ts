import { test, expect, type Page } from "@playwright/test";
import { createMockTransport } from "../src/mock/transport";
import { demoChecklist, demoRecords } from "../src/mock/fixtures";

const checklistId = demoChecklist.checklist_id;
async function routeApi(page: Page) {
  const transport = createMockTransport();
  await page.route("**/api/v1/**", async (route) => {
    const req = route.request();
    const path = req.url().split("/api/v1")[1];
    const response = await transport(path, {
      method: req.method(),
      body: req.postData(),
    });
    await route.fulfill({
      status: response.status,
      contentType: "application/json",
      body: await response.text(),
    });
  });
  return transport;
}
const criterion = (page: Page, number: number) =>
  page.getByRole("region", { name: `Критерий ${number}`, exact: true });

function captureLogs(page: Page) {
  const logs: { type: string; text: string }[] = [];
  page.on("console", (msg) => {
    logs.push({ type: msg.type(), text: msg.text() });
  });
  return logs;
}

test("explicit mock mode, language/channel filters, roles and raw timestamps", async ({
  page,
}) => {
  await page.goto("/?mode=mock");
  await expect(page.getByText("ДЕМО · СИНТЕТИЧЕСКИЕ ДАННЫЕ")).toBeVisible();
  await expect(page.locator("tbody tr")).toHaveCount(3);
  await page
    .getByRole("combobox", { name: "Язык", exact: true })
    .selectOption("KZ");
  await expect(page.locator("tbody tr")).toHaveCount(1);
  await page.getByRole("link", { name: "SYN-KZ-002", exact: true }).click();
  await expect(
    page.getByText("Сәлеметсіз бе! Құжаттарды қайдан табамын?"),
  ).toBeVisible();
  await expect(page.locator("time").first()).toHaveText("01:10");
  await expect(page.getByText("Оператор", { exact: true })).toBeVisible();
  await page.getByRole("link", { name: "← Все диалоги" }).click();
  await page
    .getByRole("combobox", { name: "Канал", exact: true })
    .selectOption("voice call");
  await expect(page.locator("tbody tr")).toHaveCount(1);
  await page.getByRole("link", { name: "SYN-EN-003", exact: true }).click();
  await expect(page.locator("time").first()).toHaveText("2026-11-07T10:00:01Z");
  await expect(
    page.getByRole("link", { name: "Открыть запись" }),
  ).toHaveAttribute("rel", "noopener noreferrer");
  await page
    .getByRole("combobox", { name: "Чек-лист", exact: true })
    .selectOption(checklistId);
  await expect(
    page.getByRole("combobox", { name: "Версия", exact: true }),
  ).toHaveValue(demoChecklist.id);
  await expect(
    page.getByRole("button", { name: "Запустить оценку" }),
  ).toBeVisible();
});

test("HTTP group import, synthetic envelope JSON, idempotency and pagination", async ({
  page,
}) => {
  await routeApi(page);
  await page.goto("/?mode=api");
  await page.getByLabel("Группа набора").fill("OTHER-GROUP");
  await page.getByRole("button", { name: "Импортировать группу" }).click();
  await expect(
    page.getByRole("status").filter({ hasText: "Импорт завершён" }),
  ).toContainText("новых 3");
  const records = Array.from({ length: 23 }, (_, i) => ({
    ...demoRecords[0],
    conversation_id: `UPLOAD-${i}`,
  }));
  const file = {
    name: "synthetic.json",
    mimeType: "application/json",
    buffer: Buffer.from(JSON.stringify({ isSuccess: true, data: { records } })),
  };
  await page.getByLabel("Загрузить JSON", { exact: true }).setInputFiles(file);
  await expect(
    page.getByRole("status").filter({ hasText: "Импорт завершён" }),
  ).toContainText("новых 23");
  await expect(page.locator("tbody tr")).toHaveCount(20);
  await page.getByRole("button", { name: "Далее", exact: true }).click();
  await expect(page.locator("tbody tr")).toHaveCount(9);
  await page.getByLabel("Загрузить JSON", { exact: true }).setInputFiles(file);
  await expect(
    page.getByRole("status").filter({ hasText: "Импорт завершён" }),
  ).toContainText("без изменений 23");
  await expect(page.locator("tbody tr")).toHaveCount(20);
});

test("malformed/duplicate JSON is rejected before HTTP, private values stay out of logs", async ({
  page,
}) => {
  await routeApi(page);
  const logs = captureLogs(page);
  let writes = 0;
  page.on("request", (r) => {
    if (r.method() === "POST") writes++;
  });
  await page.goto("/?mode=api");
  for (const contents of [
    '{"SYNTHETIC_SECRET":',
    JSON.stringify([demoRecords[0], demoRecords[0]]),
  ]) {
    await page.getByLabel("Загрузить JSON", { exact: true }).setInputFiles({
      name: "SYNTHETIC_SECRET.json",
      mimeType: "application/json",
      buffer: Buffer.from(contents),
    });
    await expect(page.getByRole("alert")).toContainText("Нужен JSON");
  }
  expect(writes).toBe(0);
  expect(
    logs
      .filter((l) => l.text.includes("event=file_rejected"))
      .every((l) => l.type === "warning"),
  ).toBe(true);
  expect(logs.map((l) => l.text).join()).not.toContain("SYNTHETIC_SECRET");
});

test("retry a failed load, empty filter state, safe status/code logging", async ({
  page,
}) => {
  await routeApi(page);
  const logs = captureLogs(page);
  let fail = true;
  await page.route("**/api/v1/conversations?**", async (route) => {
    if (!fail) return route.fallback();
    await route.fulfill({
      status: 503,
      json: {
        error: {
          code: "SYNTHETIC_SECRET",
          message: "private transcript SYNTHETIC_SECRET",
          details: [],
        },
      },
    });
  });
  await page.goto("/?mode=api");
  await expect(page.getByRole("alert")).toContainText("ошибки сервера");
  fail = false;
  await page.getByRole("button", { name: "Повторить запрос" }).click();
  await expect(page.locator("tbody tr")).toHaveCount(3);
  await page
    .getByRole("combobox", { name: "Язык", exact: true })
    .selectOption("RU");
  await page
    .getByRole("combobox", { name: "Канал", exact: true })
    .selectOption("voice call");
  await expect(
    page.getByRole("heading", { name: "Диалогов пока нет" }),
  ).toBeVisible();
  expect(logs.map((l) => l.text).join()).not.toContain("SYNTHETIC_SECRET");
  expect(
    logs.some(
      (l) =>
        l.type === "error" &&
        l.text === "cqa.ui event=load_failed status=503 error=INTERNAL_ERROR",
    ),
  ).toBe(true);
});

test("editor creates custom checklist, validates weight, scale and N/A, saves immutable versions", async ({
  page,
}) => {
  await routeApi(page);
  await page.goto("/checklists/new?mode=api");
  await page
    .getByLabel("Название чек-листа", { exact: true })
    .fill("Собственный чек-лист");
  const first = criterion(page, 1);
  await first.getByLabel("Название критерия").fill("Новый критерий");
  await first
    .getByLabel("Инструкция для оценки")
    .fill("Проверьте следующий шаг.");
  await first.getByLabel("Тип ответа").selectOption("scale");
  await first.getByLabel("Описание ступени 1").fill("Нет");
  await first.getByLabel("Описание ступени 2").fill("Да");
  await first.getByLabel("Значение ступени 2").fill("0");
  await page
    .getByRole("button", { name: "Создать чек-лист", exact: true })
    .click();
  await expect(page.getByRole("alert")).toContainText(
    "разные неотрицательные ступени",
  );
  await first.getByLabel("Значение ступени 2").fill("7");
  await first.getByLabel("Вес", { exact: true }).fill("");
  await page
    .getByRole("button", { name: "Создать чек-лист", exact: true })
    .click();
  await expect(page.getByRole("alert")).toContainText("Вес:");
  await first.getByLabel("Вес", { exact: true }).fill("25");
  await first.getByLabel("Разрешить N/A").check();
  await page
    .getByRole("button", { name: "Создать чек-лист", exact: true })
    .click();
  await expect(page.getByRole("alert")).toContainText("Укажите условие");
  await first
    .getByLabel("Условие применения N/A")
    .fill("Запрос не сформулирован");
  await page.getByRole("button", { name: "+ Добавить критерий" }).click();
  const second = criterion(page, 2);
  await second.getByLabel("Название критерия").fill("Пользовательский флаг");
  await second.getByLabel("Инструкция для оценки").fill("Проверьте нарушение");
  await second.getByLabel("Тип ответа").selectOption("flag");
  await expect(second.getByLabel("Вес", { exact: true })).toHaveValue("0");
  await expect(second.getByLabel("Разрешить N/A")).toBeDisabled();
  await page
    .getByRole("button", { name: "Создать чек-лист", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Собственный чек-лист" }),
  ).toBeVisible();
  await criterion(page, 1).getByLabel("Вес", { exact: true }).fill("30");
  await page.getByRole("button", { name: "Сохранить новую версию" }).click();
  await expect(page.getByRole("status")).toContainText("Версия 2 сохранена");
  const select = page.getByLabel("История версий");
  await select.selectOption({ label: "Версия 1" });
  await expect(
    criterion(page, 1).getByLabel("Вес", { exact: true }),
  ).toHaveValue("25");
});

test("409 retains draft, compares latest and sends explicit new expected version", async ({
  page,
}) => {
  const transport = await routeApi(page);
  const logs = captureLogs(page);
  await page.goto(`/checklists/${checklistId}?mode=api`);
  await criterion(page, 1).getByLabel("Название критерия").fill("Мой черновик");
  await transport(`/checklists/${checklistId}/versions`, {
    method: "POST",
    body: JSON.stringify({
      expected_version_number: 1,
      criteria: demoChecklist.criteria.map((c) => ({
        ...c,
        name: `Изменено: ${c.name}`,
      })),
    }),
  });
  await page.getByRole("button", { name: "Сохранить новую версию" }).click();
  await expect(page.getByRole("alert")).toContainText("Появилась новая версия");
  await expect(criterion(page, 1).getByLabel("Название критерия")).toHaveValue(
    "Мой черновик",
  );
  await page
    .getByRole("button", { name: "Показать актуальную версию" })
    .click();
  await expect(
    page.getByRole("heading", { name: "Актуальная версия 2" }),
  ).toBeVisible();
  await page.getByText("Посмотреть сохранённые критерии").click();
  await expect(
    page.getByText("Изменено: Начало разговора", { exact: true }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Сохранить черновик для повторной отправки" })
    .press("Enter");
  await expect(criterion(page, 1).getByLabel("Название критерия")).toHaveValue(
    "Мой черновик",
  );
  await page.getByRole("button", { name: "Сохранить новую версию" }).click();
  await expect(page.getByRole("status")).toContainText("Версия 3 сохранена");
  expect(
    logs.filter(
      (l) =>
        l.text === "cqa.ui event=save_failed status=409 error=VERSION_CONFLICT",
    ),
  ).toHaveLength(1);
  expect(logs.some((l) => l.text.includes("event=save_completed"))).toBe(true);
});

test("navigation protects unsaved fields", async ({ page }) => {
  await page.goto("/checklists/new?mode=mock");
  await page
    .getByLabel("Название чек-листа", { exact: true })
    .fill("Не терять");
  await page.getByRole("link", { name: "← Все чек-листы" }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.getByRole("button", { name: "Остаться" }).click();
  await expect(
    page.getByLabel("Название чек-листа", { exact: true }),
  ).toHaveValue("Не терять");
  await page.getByRole("link", { name: "← Все чек-листы" }).click();
  await page.getByRole("button", { name: "Покинуть редактор" }).click();
  await expect(
    page.getByRole("heading", { name: "Чек-листы", exact: true }),
  ).toBeVisible();
});

test("contract drift in checklist summaries is visible, never replaced by mock data", async ({
  page,
}) => {
  await routeApi(page);
  await page.route("**/api/v1/checklists?**", (route) =>
    route.fulfill({
      json: {
        items: [
          {
            id: checklistId,
            name: "Server",
            current_version_id: demoChecklist.id,
            current_version_number: 1,
          },
        ],
        total: 1,
        limit: 100,
        offset: 0,
      },
    }),
  );
  await page.goto("/checklists?mode=api");
  await expect(page.getByRole("alert")).toContainText(
    "не соответствует общему контракту",
  );
  await expect(page.getByText("ДЕМО · СИНТЕТИЧЕСКИЕ ДАННЫЕ")).toHaveCount(0);
  await expect(
    page.getByRole("link", { name: "Качество консультации", exact: false }),
  ).toHaveCount(0);
});

test("API import/save failures preserve form and do not announce success", async ({
  page,
}) => {
  await routeApi(page);
  const logs = captureLogs(page);
  await page.route("**/api/v1/datasets/import", (route) =>
    route.fulfill({
      status: 502,
      json: {
        error: {
          code: "DATASET_ERROR",
          message: "SYNTHETIC_SECRET",
          details: [],
        },
      },
    }),
  );
  await page.goto("/?mode=api");
  await page.getByRole("button", { name: "Импортировать группу" }).click();
  await expect(page.getByRole("alert")).toContainText(
    "Не удалось получить набор",
  );
  await expect(
    page.getByText("Импорт завершён:", { exact: false }),
  ).toHaveCount(0);
  await page.goto(`/checklists/${checklistId}?mode=api`);
  await page.route("**/api/v1/checklists/*/versions", (route) =>
    route.fulfill({
      status: 422,
      json: {
        error: {
          code: "VALIDATION_ERROR",
          message: "SYNTHETIC_SECRET",
          details: [
            { loc: ["body", "SYNTHETIC_SECRET"], type: "SYNTHETIC_SECRET" },
          ],
        },
      },
    }),
  );
  await criterion(page, 1).getByLabel("Вес", { exact: true }).fill("33");
  await page.getByRole("button", { name: "Сохранить новую версию" }).click();
  await expect(page.getByRole("alert")).toContainText("Сервер отклонил данные");
  await expect(
    criterion(page, 1).getByLabel("Вес", { exact: true }),
  ).toHaveValue("33");
  expect(logs.map((l) => l.text).join()).not.toContain("SYNTHETIC_SECRET");
  expect(
    logs.some(
      (l) =>
        l.text.includes(
          "event=save_failed status=422 error=VALIDATION_ERROR",
        ) && l.type === "warning",
    ),
  ).toBe(true);
});

test("mode switch uses actual HTTP and persists explicit choice on reload", async ({
  page,
}) => {
  await routeApi(page);
  let requests = 0;
  page.on("request", (req) => {
    if (req.url().includes("/api/v1/")) requests++;
  });
  await page.goto("/?mode=mock");
  await expect(page.locator("tbody tr")).toHaveCount(3);
  expect(requests).toBe(0);
  await page.getByLabel("Источник данных").selectOption("api");
  await expect(page.getByText("ДЕМО · СИНТЕТИЧЕСКИЕ ДАННЫЕ")).toHaveCount(0);
  await expect(page.locator("tbody tr")).toHaveCount(3);
  expect(requests).toBeGreaterThan(0);
  await page.reload();
  await expect(page.getByLabel("Источник данных")).toHaveValue("api");
});

test("mobile layout and keyboard entry", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/?mode=mock");
  await expect(
    page.getByRole("heading", { name: "Каждый диалог — в деталях" }),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  await page.keyboard.press("Tab");
  await expect(
    page.getByRole("link", { name: "Перейти к содержимому" }),
  ).toBeFocused();
  await page.keyboard.press("Enter");
  await page.getByRole("link", { name: "SYN-RU-001", exact: true }).click();
  await expect(page.getByText("Бот", { exact: true })).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
});

test("failed conflict refresh remains retryable without losing the draft", async ({
  page,
}) => {
  const transport = await routeApi(page);
  await page.goto(`/checklists/${checklistId}?mode=api`);
  await criterion(page, 1)
    .getByLabel("Название критерия")
    .fill("Сохранить ввод");
  await transport(`/checklists/${checklistId}/versions`, {
    method: "POST",
    body: JSON.stringify({
      expected_version_number: 1,
      criteria: demoChecklist.criteria,
    }),
  });
  await page.getByRole("button", { name: "Сохранить новую версию" }).click();
  await expect(page.getByRole("alert")).toContainText("Появилась новая версия");
  await page.route(`**/api/v1/checklists/${checklistId}`, (route) =>
    route.abort(),
  );
  await page
    .getByRole("button", { name: "Показать актуальную версию" })
    .click();
  await expect(
    page.getByText("Нет соединения с сервером.", { exact: false }),
  ).toBeVisible();
  await page.unroute(`**/api/v1/checklists/${checklistId}`);
  await page.getByRole("button", { name: "Повторить запрос" }).click();
  await expect(
    page.getByRole("heading", { name: "Актуальная версия 2" }),
  ).toBeVisible();
  await expect(criterion(page, 1).getByLabel("Название критерия")).toHaveValue(
    "Сохранить ввод",
  );
});

test("out-of-order filter loads cannot replace the latest result", async ({
  page,
}) => {
  await routeApi(page);
  await page.route("**/api/v1/conversations?**", async (route) => {
    if (route.request().url().includes("language=RU"))
      await new Promise((resolve) => setTimeout(resolve, 400));
    await route.fallback();
  });
  await page.goto("/?mode=api");
  await expect(page.locator("tbody tr")).toHaveCount(3);
  await page
    .getByRole("combobox", { name: "Язык", exact: true })
    .selectOption("RU");
  await expect(
    page.getByRole("status").filter({ hasText: "Загружаем данные" }),
  ).toBeVisible();
  await page
    .getByRole("combobox", { name: "Язык", exact: true })
    .selectOption("KZ");
  await expect(
    page.getByRole("link", { name: "SYN-KZ-002", exact: true }),
  ).toBeVisible();
  await page.waitForTimeout(500); // Let the deliberately slower stale response complete.
  await expect(page.locator("tbody tr")).toHaveCount(1);
  await expect(
    page.getByRole("link", { name: "SYN-KZ-002", exact: true }),
  ).toBeVisible();
});

test("client timeout/network errors have one safe log and no automatic mutation retries", async ({
  page,
}) => {
  const logs = captureLogs(page);
  await page.goto("/?mode=mock");
  const result = await page.evaluate(async () => {
    const path = "/src/api/client.ts";
    const { ApiClient } = await import(path);
    let calls = 0;
    const client = new ApiClient((_path: string, init: RequestInit) => {
      calls++;
      return new Promise((_resolve, reject) =>
        init.signal!.addEventListener(
          "abort",
          () => reject(new Error("SYNTHETIC_SECRET")),
          { once: true },
        ),
      );
    }, 20);
    let code = "";
    try {
      await client.importGroup("SYNTHETIC_SECRET");
    } catch (e) {
      code = (e as { code: string }).code;
    }
    return { calls, code };
  });
  expect(result).toEqual({ calls: 1, code: "TIMEOUT" });
  expect(
    logs.filter(
      (l) => l.text === "cqa.ui event=import_failed status=0 error=TIMEOUT",
    ),
  ).toHaveLength(1);
  expect(logs.map((l) => l.text).join()).not.toContain("SYNTHETIC_SECRET");
});

test("unexpected render error shows safe fallback without logging Error content", async ({
  page,
}) => {
  const logs = captureLogs(page);
  await page.addInitScript(() => {
    Object.defineProperty(window, "sessionStorage", {
      get() {
        throw new Error("SYNTHETIC_SECRET");
      },
    });
  });
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "Не удалось показать экран" }),
  ).toBeVisible();
  expect(
    logs.some(
      (l) => l.text === "cqa.ui event=ui_failed status=0 error=UI_ERROR",
    ),
  ).toBe(true);
  expect(logs.map((l) => l.text).join()).not.toContain("SYNTHETIC_SECRET");
});

test("timeout while reading a successful response body stays TIMEOUT, not a schema error", async ({
  page,
}) => {
  const logs = captureLogs(page);
  await page.goto("/?mode=mock");
  const result = await page.evaluate(async () => {
    const path = "/src/api/client.ts";
    const { ApiClient } = await import(path);
    let calls = 0;
    const client = new ApiClient((_path: string, init: RequestInit) => {
      calls++;
      const body = new ReadableStream({
        start(controller) {
          controller.enqueue(new TextEncoder().encode('{"group_id":'));
          init.signal!.addEventListener(
            "abort",
            () =>
              controller.error(
                new DOMException("SYNTHETIC_SECRET", "AbortError"),
              ),
            { once: true },
          );
        },
      });
      return Promise.resolve(new Response(body, { status: 200 }));
    }, 20);
    try {
      await client.importGroup("SYNTHETIC_SECRET");
      return { calls, code: "unexpected_success", message: "" };
    } catch (e) {
      const safe = e as { code: string; message: string };
      return { calls, code: safe.code, message: safe.message };
    }
  });
  expect(result.calls).toBe(1);
  expect(result.code).toBe("TIMEOUT");
  expect(result.message).toContain("Проверьте результат операции");
  expect(
    logs.filter(
      (l) => l.text === "cqa.ui event=import_failed status=0 error=TIMEOUT",
    ),
  ).toHaveLength(1);
  expect(logs.map((l) => l.text).join()).not.toContain("SYNTHETIC_SECRET");
});

test("late create completion never navigates back after confirmed departure", async ({
  page,
}) => {
  await routeApi(page);
  let release!: () => void;
  const pending = new Promise<void>((resolve) => {
    release = resolve;
  });
  let received!: () => void;
  const started = new Promise<void>((resolve) => {
    received = resolve;
  });
  await page.route("**/api/v1/checklists", async (route) => {
    if (route.request().method() !== "POST") return route.fallback();
    received();
    await pending;
    await route.fallback();
  });
  await page.goto("/checklists/new?mode=api");
  await page
    .getByLabel("Название чек-листа", { exact: true })
    .fill("Отложенное создание");
  await criterion(page, 1)
    .getByLabel("Название критерия")
    .fill("Свой критерий");
  await criterion(page, 1)
    .getByLabel("Инструкция для оценки")
    .fill("Проверьте ответ.");
  await page
    .getByRole("button", { name: "Создать чек-лист", exact: true })
    .click();
  await started;
  await page
    .getByRole("navigation")
    .getByRole("link", { name: "Диалоги", exact: true })
    .click();
  await expect(page.getByRole("dialog")).toContainText(
    "может завершиться после ухода",
  );
  await page.getByRole("button", { name: "Покинуть редактор" }).click();
  await expect(page).toHaveURL(/\/conversations$/);
  const done = page.waitForResponse(
    (response) =>
      response.url().endsWith("/api/v1/checklists") &&
      response.request().method() === "POST",
  );
  release();
  await done;
  await expect(
    page.getByRole("heading", { name: "Каждый диалог — в деталях" }),
  ).toBeVisible();
  await expect(page).toHaveURL(/\/conversations$/);
  await page
    .getByRole("navigation")
    .getByRole("link", { name: "Чек-листы", exact: true })
    .click();
  await expect(
    page.getByRole("link", { name: /Отложенное создание/ }),
  ).toBeVisible();
});
